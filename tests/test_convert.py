"""Smoke tests for the conversion layer.

These run without Java or a built jar by mocking the subprocess boundary.
If a real ``vendor/hwp2hwpx.jar`` *and* a sample ``tests/fixtures/*.hwp`` are
present, an end-to-end conversion test additionally checks that the output is
a valid ZIP (HWPX is a ZIP container).
"""

from __future__ import annotations

import subprocess
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

    def fake_run(cmd, capture_output, text):  # noqa: ANN001
        captured["cmd"] = cmd
        out.write_bytes(b"PK\x03\x04")  # pretend the converter wrote output
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    backend = Hwp2HwpxBackend(jar_path=jar, normalize=False)
    result = backend.convert(src, out)

    assert captured["cmd"] == ["java", "-jar", str(jar), str(src), str(out)]
    assert result.ok is True
    assert out.parent.is_dir()  # parent created for us


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


def test_normalize_adds_declared_but_missing_preview(tmp_path: Path) -> None:
    from hwp_agent.convert.hwp2hwpx_backend import _normalize_hwpx

    pkg = tmp_path / "doc.hwpx"
    _write_hwpx_missing_preview(pkg)

    added = _normalize_hwpx(pkg)
    assert added == ("Preview/PrvText.txt",)
    with zipfile.ZipFile(pkg) as zf:
        assert "Preview/PrvText.txt" in zf.namelist()

    # idempotent: a second pass adds nothing
    assert _normalize_hwpx(pkg) == ()


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
