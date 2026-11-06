"""Smoke tests for the conversion layer.

These run without Java or a built jar by mocking the subprocess boundary.
If a real ``vendor/hwp2hwpx.jar`` *and* a sample ``tests/fixtures/*.hwp`` are
present, an end-to-end conversion test additionally checks that the output is
a valid ZIP (HWPX is a ZIP container).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from hwp_agent.convert import ConvertResult, Hwp2HwpxBackend

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_JAR = REPO_ROOT / "vendor" / "hwp2hwpx.jar"
FIXTURES = REPO_ROOT / "tests" / "fixtures"


# --- ConvertResult --------------------------------------------------------


def test_convert_result_ok_requires_zero_exit_and_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    out.write_bytes(b"PK\x03\x04")
    assert ConvertResult(out, "x", returncode=0).ok is True
    assert ConvertResult(out, "x", returncode=1).ok is False
    assert ConvertResult(tmp_path / "missing.hwpx", "x", returncode=0).ok is False


# --- backend wiring (no Java needed) --------------------------------------


def test_jar_resolution_prefers_explicit_then_env(tmp_path: Path, monkeypatch) -> None:
    explicit = tmp_path / "explicit.jar"
    assert Hwp2HwpxBackend(jar_path=explicit).jar_path == explicit

    env_jar = tmp_path / "env.jar"
    monkeypatch.setenv("HWP2HWPX_JAR", str(env_jar))
    assert Hwp2HwpxBackend().jar_path == env_jar


def test_is_available_false_when_jar_missing(tmp_path: Path) -> None:
    backend = Hwp2HwpxBackend(jar_path=tmp_path / "nope.jar")
    assert backend.is_available() is False


def test_convert_builds_expected_java_command(tmp_path: Path, monkeypatch) -> None:
    jar = tmp_path / "hwp2hwpx.jar"
    jar.write_bytes(b"fake")
    src = tmp_path / "in.hwp"
    src.write_bytes(b"\xd0\xcf\x11\xe0")  # OLE magic, content irrelevant here
    out = tmp_path / "sub" / "in.hwpx"

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, capture_output, **kw):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["text"] = kw.get("text")
        Path(cmd[-1]).write_bytes(b"PK\x03\x04")  # pretend the converter wrote output
        # bytes, decoded by the backend (Java on a Korean Windows prints cp949)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr="경고".encode("cp949"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    backend = Hwp2HwpxBackend(jar_path=jar, normalize=False)
    result = backend.convert(src, out)

    java, flag, jar_arg, src_arg, out_arg = captured["cmd"]
    assert (java, flag, jar_arg) == ("java", "-jar", str(jar))
    # Java gets ASCII staging copies, never the user's paths (hwp-agent#89)
    assert src_arg.endswith("in.hwp") and out_arg.endswith("out.hwpx")
    assert src_arg != str(src) and src_arg.isascii() and out_arg.isascii()
    assert not captured["text"]  # not the locale's codec (hwp-agent#83)
    assert result.ok is True and isinstance(result.stderr, str) and result.stdout == ""
    assert out.parent.is_dir()  # parent created for us


# --- non-ASCII paths (hwp-agent#89) ----------------------------------------
# Java reads its command line in the Windows ANSI code page, so a path outside it reaches
# the JVM as "?". The backend stages ASCII copies and maps messages back to real names.

ODD_NAME = "한글 경로 🙂"


def _fake_java(record: dict, *, rc: int = 0, msg: bytes = b""):
    def fake_run(cmd, capture_output, **kw):  # noqa: ANN001
        record["cmd"] = cmd
        record["src_bytes"] = Path(cmd[3]).read_bytes()
        if rc == 0:
            Path(cmd[4]).write_bytes(b"PK\x03\x04")
        err = msg or f"cannot read {cmd[3]}".encode()
        return subprocess.CompletedProcess(cmd, rc, stdout=b"", stderr=err)

    return fake_run


def test_convert_stages_non_ascii_paths(tmp_path: Path, monkeypatch) -> None:
    jar = tmp_path / ODD_NAME / "hwp2hwpx.jar"
    jar.parent.mkdir()
    jar.write_bytes(b"fake")
    src = tmp_path / ODD_NAME / "보고서 🙂.HWP"
    src.write_bytes(b"\xd0\xcf\x11\xe0 payload")
    out = tmp_path / ODD_NAME / "결과" / "보고서 🙂.hwpx"
    rec: dict = {}
    monkeypatch.setattr(subprocess, "run", _fake_java(rec))

    result = Hwp2HwpxBackend(jar_path=jar, normalize=False).convert(src, out)

    assert all(a.isascii() for a in rec["cmd"][2:]), rec["cmd"]  # jar staged too
    assert rec["src_bytes"] == src.read_bytes()
    assert result.ok and out.read_bytes() == b"PK\x03\x04"
    assert result.hwpx_path == out
    assert not Path(rec["cmd"][3]).parent.exists()  # staging dir cleaned up


def test_convert_failure_names_the_original_path(tmp_path: Path, monkeypatch) -> None:
    jar = tmp_path / "hwp2hwpx.jar"
    jar.write_bytes(b"fake")
    src = tmp_path / f"{ODD_NAME}.hwp"
    src.write_bytes(b"x")
    out = tmp_path / f"{ODD_NAME}.hwpx"
    rec: dict = {}
    monkeypatch.setattr(subprocess, "run", _fake_java(rec, rc=1))

    result = Hwp2HwpxBackend(jar_path=jar).convert(src, out)

    assert not result.ok and not out.exists()
    assert str(src) in result.stderr and rec["cmd"][3] not in result.stderr


def test_cli_convert_error_shows_original_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    from hwp_agent.cli.main import main

    jar = tmp_path / "hwp2hwpx.jar"
    jar.write_bytes(b"fake")
    src = tmp_path / f"{ODD_NAME}.hwp"
    src.write_bytes(b"x")
    out = tmp_path / f"{ODD_NAME}.hwpx"
    monkeypatch.setattr(subprocess, "run", _fake_java({}, rc=1, msg=b"boom"))
    monkeypatch.setattr(Hwp2HwpxBackend, "has_java", lambda self: True)

    rc = main(["convert", str(src), str(out), "--jar", str(jar)])

    assert rc == 1
    err = capsys.readouterr().err
    assert str(src) in err and str(out) in err


def test_make_stage_skips_non_ascii_temp(tmp_path: Path, monkeypatch) -> None:
    from hwp_agent.convert import hwp2hwpx_backend as hb

    odd = tmp_path / ODD_NAME
    odd.mkdir()
    ok = tmp_path / "public"
    ok.mkdir()
    monkeypatch.setattr(hb.tempfile, "gettempdir", lambda: str(odd))
    monkeypatch.setenv("PUBLIC", str(ok))
    with hb._make_stage() as d:
        assert Path(d).parent == ok


def _skip_or_fail(reason: str) -> None:
    # CI (windows-convert.yml) sets this so a missing prerequisite cannot pass as a skip.
    if os.environ.get("HWP_AGENT_REQUIRE_JAVA"):
        pytest.fail(reason)
    pytest.skip(reason)


def test_real_java_converts_non_ascii_path(tmp_path: Path) -> None:
    """End-to-end through the real jar. On a Windows runner whose ANSI code page cannot
    represent Hangul or emoji (windows-latest is cp1252) this failed before #89."""
    java = shutil.which("java")
    if java is None or not VENDOR_JAR.is_file():
        _skip_or_fail("needs java on PATH and vendor/hwp2hwpx.jar")
    blank = tmp_path / "blank.hwp"
    gen = subprocess.run(
        [java, "-cp", str(VENDOR_JAR), str(REPO_ROOT / "tests" / "data" / "MakeBlankHwp.java"),
         str(blank)],
        capture_output=True,
    )
    if gen.returncode != 0 or not blank.is_file():
        _skip_or_fail(f"cannot generate a blank .hwp (needs a JDK): {gen.stderr[-300:]!r}")

    folder = tmp_path / ODD_NAME
    folder.mkdir()
    src = folder / "보고서 🙂.hwp"
    src.write_bytes(blank.read_bytes())
    out = folder / "보고서 🙂.hwpx"

    result = Hwp2HwpxBackend(jar_path=VENDOR_JAR).convert(src, out)

    assert result.ok, result.stderr
    assert zipfile.is_zipfile(out)

    if sys.platform == "win32" and not _ansi_encodable(str(src)):
        # Control: the same paths handed to Java directly still break on this machine,
        # so the pass above is the staging fix at work, not a forgiving environment.
        direct = folder / "direct.hwpx"
        raw = subprocess.run([java, "-jar", str(VENDOR_JAR), str(src), str(direct)],
                             capture_output=True)
        assert raw.returncode != 0 or not direct.is_file(), "Java read a non-ANSI path?"


def _ansi_encodable(text: str) -> bool:
    try:
        text.encode("mbcs", "strict")  # Windows only: the ANSI code page, no best fit
    except UnicodeEncodeError:
        return False
    return True


def test_convert_raises_on_missing_input(tmp_path: Path) -> None:
    jar = tmp_path / "hwp2hwpx.jar"
    jar.write_bytes(b"fake")
    backend = Hwp2HwpxBackend(jar_path=jar)
    with pytest.raises(FileNotFoundError):
        backend.convert(tmp_path / "absent.hwp", tmp_path / "out.hwpx")


# --- normalization (no Java needed) ---------------------------------------


_CONTAINER_XML = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    b'<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container">'
    b"<ocf:rootfiles>"
    b'<ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>'
    b'<ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/>'
    b"</ocf:rootfiles></ocf:container>"
)


def _write_hwpx_missing_preview(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        zf.writestr("Contents/content.hpf", "<hpf/>")  # declared part present


def _write_hwpx(path: Path, entries: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        for name, content in entries.items():
            zf.writestr(name, content)


def test_normalize_adds_declared_but_missing_preview(tmp_path: Path) -> None:
    from hwp_agent.convert.hwp2hwpx_backend import _normalize_hwpx

    pkg = tmp_path / "doc.hwpx"
    _write_hwpx_missing_preview(pkg)

    changes = _normalize_hwpx(pkg)
    assert changes == ("preview:Preview/PrvText.txt",)
    with zipfile.ZipFile(pkg) as zf:
        assert "Preview/PrvText.txt" in zf.namelist()
        assert zf.namelist()[0] == "mimetype"  # mimetype stays first after rewrite

    # idempotent: a second pass changes nothing
    assert _normalize_hwpx(pkg) == ()


def test_normalize_fixes_inflated_linespacing(tmp_path: Path) -> None:
    from hwp_agent.convert.hwp2hwpx_backend import _normalize_hwpx

    header = (
        '<?xml version="1.0"?><hh:head xmlns:hh="x">'
        '<hh:paraPr id="1"><hh:lineSpacing type="PERCENT" value="1600" unit="HWPUNIT"/></hh:paraPr>'
        '<hh:paraPr id="2"><hh:lineSpacing type="PERCENT" value="150" unit="HWPUNIT"/></hh:paraPr>'
        "</hh:head>"
    )
    pkg = tmp_path / "doc.hwpx"
    _write_hwpx(pkg, {"Contents/header.xml": header})

    changes = _normalize_hwpx(pkg)
    assert changes == ("linespacing:1",)
    with zipfile.ZipFile(pkg) as zf:
        out = zf.read("Contents/header.xml").decode("utf-8")
    assert 'value="160"' in out  # 1600 -> 160
    assert 'value="150"' in out  # legitimate value untouched
    assert _normalize_hwpx(pkg) == ()  # idempotent


def test_normalize_fixes_table_pagebreak(tmp_path: Path) -> None:
    from hwp_agent.convert.hwp2hwpx_backend import _normalize_hwpx

    section = (
        '<?xml version="1.0"?><hs:sec xmlns:hs="x" xmlns:hp="y">'
        '<hp:p pageBreak="0"><hp:tbl pageBreak="TABLE"/></hp:p>'
        '<hp:p pageBreak="1"><hp:tbl pageBreak="TABLE"/></hp:p>'
        "</hs:sec>"
    )
    pkg = tmp_path / "doc.hwpx"
    _write_hwpx(pkg, {"Contents/section0.xml": section})

    changes = _normalize_hwpx(pkg)
    assert changes == ("pagebreak:2",)
    with zipfile.ZipFile(pkg) as zf:
        out = zf.read("Contents/section0.xml").decode("utf-8")
    assert 'pageBreak="TABLE"' not in out
    assert out.count('pageBreak="CELL"') == 2
    assert 'pageBreak="0"' in out and 'pageBreak="1"' in out  # paragraphs untouched
    assert _normalize_hwpx(pkg) == ()  # idempotent


# --- optional end-to-end --------------------------------------------------


def _first_sample_hwp() -> Path | None:
    if not FIXTURES.is_dir():
        return None
    return next(iter(sorted(FIXTURES.glob("*.hwp"))), None)


@pytest.mark.skipif(not VENDOR_JAR.is_file(), reason="vendor/hwp2hwpx.jar not built")
def test_end_to_end_produces_openable_hwpx(tmp_path: Path) -> None:
    sample = _first_sample_hwp()
    if sample is None:
        pytest.skip("no tests/fixtures/*.hwp sample available")

    out = tmp_path / "converted.hwpx"
    result = Hwp2HwpxBackend().convert(sample, out)
    assert result.ok, result.stderr
    assert zipfile.is_zipfile(out), "HWPX output is not a valid ZIP container"

    # hwp2hwpx omits the Preview part it declares; normalization must fix it
    # so the package opens under a strict OPC reader (python-hwpx).
    from hwpx.document import HwpxDocument

    doc = HwpxDocument.open(out)
    assert len(doc.sections) >= 1

    # Regression guard for the hwplib surrogate-pair patch: non-BMP characters
    # must survive conversion rather than degrade to U+FFFD replacement chars.
    with zipfile.ZipFile(out) as zf:
        section_bytes = b"".join(
            zf.read(n) for n in zf.namelist() if n.startswith("Contents/section")
        )
    assert b"\xef\xbf\xbd" not in section_bytes, "non-BMP chars corrupted to U+FFFD"
