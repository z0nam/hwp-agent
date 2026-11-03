"""hwp2pdf-cli backend and the engine chain (local CLI → server → rhwp).

The CLI is a fake executable (a Python script with a shebang) that mimics
hwp2pdf's contract: ``<target> --pdf|--docx`` writes ``<stem>.<ext>`` next to
the target, exit 0; a running Hangul → ``ERROR: HWP is already running`` exit 1.
Windows-only gating is patched so the suite runs on any OS.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from hwp_agent.cli.main import main
from hwp_agent.render import (
    LocalHwp2PdfBackend,
    LocalRhwpBackend,
    render_document,
    resolve_engine,
    resolve_hwp2pdf_cli,
    select_render_backend,
)

FAKE_CLI = '''\
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = os.environ.get("FAKE_H2P_LOG")
if log:
    Path(log).write_text(json.dumps({
        "args": args,
        "server_url": os.environ.get("HWP2PDF_SERVER_URL"),
        "token": os.environ.get("HWP2PDF_TOKEN"),
        "local": os.environ.get("HWP2PDF_LOCAL"),
    }))
mode = os.environ.get("FAKE_H2P_MODE", "ok")
if mode == "running":
    print("ERROR: HWP is already running. Close it first, or run with --kill-hwp / "
          "--allow-running-hwp. Detected: PID 4242", file=sys.stderr)
    sys.exit(1)
if mode == "fail":
    print("변환 실패: 문서를 열 수 없습니다")
    sys.exit(2)
if mode == "startfail":  # measured: consent window up → COM gives up (Hancom 2022)
    print("HWPFrame.HwpObject 시작 중...")
    print("ERROR: 한컴오피스 한글을 시작하지 못했습니다")
    print("상세: (-2146959355, '서버 실행에 실패했습니다.', None, None)")
    sys.exit(1)
if mode == "cp949":  # the frozen Windows exe prints the ANSI code page
    sys.stdout.buffer.write("ERROR: 한글 서버 실행에 실패했습니다\\n".encode("cp949"))
    sys.exit(1)
target = Path(args[0])
for flag, ext in (("--pdf", ".pdf"), ("--docx", ".docx")):
    if flag in args:
        target.with_suffix(ext).write_bytes(b"%PDF-1.7 hancom" if ext == ".pdf" else b"PK docx")
print("변환 완료")
'''


@pytest.fixture
def fake_cli(tmp_path: Path, monkeypatch) -> Path:
    exe = tmp_path / "bin" / "hwp2pdf-cli"
    exe.parent.mkdir()
    if os.name == "nt":  # no shebangs: a .cmd shim runs the script
        script = exe.with_suffix(".py")
        script.write_text(FAKE_CLI, encoding="utf-8")
        exe = exe.with_suffix(".cmd")
        exe.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        exe.write_text(f"#!{sys.executable}\n{FAKE_CLI}", encoding="utf-8")
        exe.chmod(0o755)
    monkeypatch.setenv("HWP2PDF_CLI", str(exe))
    monkeypatch.setattr("hwp_agent.render.local_hwp2pdf._is_windows", lambda: True)
    monkeypatch.setenv("FAKE_H2P_LOG", str(tmp_path / "cli-call.json"))
    return exe


def _src(tmp_path: Path, name: str = "보고서.hwp") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xd0\xcf\x11\xe0 fake hwp")
    return p


def _call(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "cli-call.json").read_text())


def _fake_pdf(_src, out) -> None:
    Path(out).write_bytes(b"%PDF-1.4 rhwp\n")


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def test_resolve_cli_env_then_path(tmp_path: Path, monkeypatch) -> None:
    assert resolve_hwp2pdf_cli() is None  # conftest: nothing installed
    # PATH lookup on Windows only matches a PATHEXT extension
    on_path = tmp_path / "pathdir" / f"no-such-hwp2pdf-cli{'.exe' if os.name == 'nt' else ''}"
    on_path.parent.mkdir()
    on_path.write_text("#!/bin/sh\n")
    on_path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{on_path.parent}{os.pathsep}{os.environ['PATH']}")
    found = resolve_hwp2pdf_cli()  # $HWP2PDF_CLI missing → PATH
    assert found and os.path.normcase(found) == os.path.normcase(str(on_path))
    explicit = tmp_path / "x-cli"
    explicit.write_text("")
    monkeypatch.setenv("HWP2PDF_CLI", str(explicit))
    assert resolve_hwp2pdf_cli() == str(explicit)  # $HWP2PDF_CLI wins


def test_local_unavailable_off_windows(fake_cli, monkeypatch) -> None:
    monkeypatch.setattr("hwp_agent.render.local_hwp2pdf._is_windows", lambda: False)
    st = LocalHwp2PdfBackend().status()
    assert not st.usable and "Windows" in st.reason


# --------------------------------------------------------------------------- #
# LocalHwp2PdfBackend
# --------------------------------------------------------------------------- #
def test_local_pdf_ok_and_never_remote(tmp_path: Path, fake_cli, monkeypatch) -> None:
    monkeypatch.setenv("HWP2PDF_SERVER_URL", "http://elsewhere:17650")
    monkeypatch.setenv("HWP2PDF_TOKEN", "secret")
    out = tmp_path / "out" / "o.pdf"
    r = LocalHwp2PdfBackend().render(_src(tmp_path), out, fmt="pdf")
    assert r.ok and r.backend == "hwp2pdf-cli" and r.authoritative and not r.remote
    assert out.read_bytes().startswith(b"%PDF-1.7 hancom")
    call = _call(tmp_path)
    assert "--pdf" in call["args"] and "--docx" not in call["args"]
    # never asks hwp2pdf to touch the user's Hangul
    assert "--kill-hwp" not in call["args"] and "--allow-running-hwp" not in call["args"]
    # runs the local engine: server env stripped, local forced
    assert call["server_url"] is None and call["token"] is None and call["local"] == "1"
    # the source is staged in a temp copy; nothing is written next to it
    assert not (tmp_path / "보고서.pdf").exists()


def test_local_docx(tmp_path: Path, fake_cli) -> None:
    out = tmp_path / "o.docx"
    r = LocalHwp2PdfBackend().render(_src(tmp_path), out, fmt="docx")
    assert r.ok and out.read_bytes() == b"PK docx"
    assert "--docx" in _call(tmp_path)["args"]


def test_local_hangul_running_is_busy(tmp_path: Path, fake_cli, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "running")
    r = LocalHwp2PdfBackend().render(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf")
    assert not r.ok and r.busy and "한글이 실행 중" in r.stderr


def test_local_failure_reports_exit_and_tail(tmp_path: Path, fake_cli, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "fail")
    r = LocalHwp2PdfBackend().render(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf")
    assert not r.ok and not r.busy and r.returncode == 2
    assert "exit 2" in r.stderr and "문서를 열 수 없습니다" in r.stderr


def test_local_start_failure_hints_at_the_consent_window(tmp_path: Path, fake_cli,
                                                         monkeypatch) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "startfail")
    r = LocalHwp2PdfBackend().render(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf")
    assert not r.ok and not r.busy and "한글을 한 번 열어 답하세요" in r.stderr
    # and it travels to the note when the chain falls back to rhwp
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", render_fn=_fake_pdf)
    assert r.ok and r.backend == "rhwp" and "동의창" in r.note


def test_local_document_failure_has_no_consent_hint(tmp_path: Path, fake_cli, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "fail")  # the document, not Hangul
    r = LocalHwp2PdfBackend().render(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf")
    assert "동의창" not in r.stderr


def test_local_cp949_output_is_readable(tmp_path: Path, fake_cli, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "cp949")
    monkeypatch.setattr("locale.getpreferredencoding", lambda *_a: "cp949")
    r = LocalHwp2PdfBackend().render(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf")
    assert not r.ok and "서버 실행에 실패했습니다" in r.stderr


# --------------------------------------------------------------------------- #
# the chain
# --------------------------------------------------------------------------- #
def test_auto_prefers_local_cli_over_server(tmp_path: Path, fake_cli, fake_hwp2pdf) -> None:
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", render_fn=_fake_pdf)
    assert r.ok and r.backend == "hwp2pdf-cli" and r.note == ""
    assert fake_hwp2pdf.run_payloads == []  # server never asked
    assert select_render_backend("pdf").name == "hwp2pdf-cli"


def test_auto_local_busy_falls_to_server(tmp_path: Path, fake_cli, fake_hwp2pdf,
                                         monkeypatch) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "running")
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", render_fn=_fake_pdf)
    assert r.ok and r.backend == "hwp2pdf" and r.remote
    assert r.note.startswith("hwp2pdf-cli 미사용") and "한글이 실행 중" in r.note


def test_auto_all_hancom_busy_falls_to_rhwp_with_both_reasons(
    tmp_path: Path, fake_cli, fake_hwp2pdf, monkeypatch,
) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "running")
    fake_hwp2pdf.queue_depth = 1
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", render_fn=_fake_pdf)
    assert r.ok and r.backend == "rhwp" and not r.authoritative
    assert "hwp2pdf-cli 미사용" in r.note and "hwp2pdf 미사용" in r.note


def test_mac_without_cli_or_server_goes_straight_to_rhwp(tmp_path: Path) -> None:
    # the Mac case: no hwp2pdf-cli, no server — rhwp, and no noise about either
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", render_fn=_fake_pdf)
    assert r.ok and r.backend == "rhwp" and r.note == ""


def test_engine_hwp2pdf_never_uses_rhwp(tmp_path: Path, fake_cli, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_H2P_MODE", "fail")
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", engine="hwp2pdf",
                        render_fn=_fake_pdf)
    assert not r.ok and r.backend == "hwp2pdf-cli" and "exit 2" in r.stderr


def test_engine_hwp2pdf_with_nothing_installed(tmp_path: Path) -> None:
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", engine="hwp2pdf")
    assert r.returncode == 2 and "한컴 엔진 없음" in r.stderr


def test_engine_local_only_reports_its_own_reason(tmp_path: Path) -> None:
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", engine="local",
                        render_fn=_fake_pdf)
    assert not r.ok and r.backend == "hwp2pdf-cli" and r.returncode == 2


def test_engine_remote_skips_local_cli(tmp_path: Path, fake_cli, fake_hwp2pdf) -> None:
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", engine="remote")
    assert r.ok and r.backend == "hwp2pdf"
    assert not (tmp_path / "cli-call.json").exists()


def test_docx_auto_uses_local_cli(tmp_path: Path, fake_cli) -> None:
    r = render_document(_src(tmp_path), tmp_path / "o.docx", fmt="docx")
    assert r.ok and r.backend == "hwp2pdf-cli"


def test_engine_env_override(tmp_path: Path, fake_cli, monkeypatch) -> None:
    monkeypatch.setenv("HWP_AGENT_RENDER_ENGINE", "rhwp")
    assert resolve_engine(None) == "rhwp" and resolve_engine("local") == "local"
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", render_fn=_fake_pdf)
    assert r.ok and r.backend == "rhwp"
    monkeypatch.setenv("HWP_AGENT_RENDER_ENGINE", "bogus")
    with pytest.raises(ValueError):
        resolve_engine(None)


def test_cli_pdf_says_which_engine(tmp_path: Path, fake_cli, capsys) -> None:
    ret = main(["pdf", str(_src(tmp_path)), "-o", str(tmp_path / "o.pdf")])
    cap = capsys.readouterr()
    assert ret == 0 and "via hwp2pdf-cli · 한컴 · 이 PC" in cap.out


def test_cli_timeout_reaches_local_cli_without_server_config(tmp_path: Path, fake_cli) -> None:
    # no server configured (conftest): --timeout must still bound the local CLI
    ret = main(["pdf", str(_src(tmp_path)), "-o", str(tmp_path / "o.pdf"),
                "--engine", "local", "--timeout", "5"])
    args = _call(tmp_path)["args"]
    assert ret == 0 and args[args.index("--timeout") + 1] == "5"


def test_cli_docx_engine_local(tmp_path: Path, fake_cli, capsys) -> None:
    ret = main(["docx", str(_src(tmp_path)), "-o", str(tmp_path / "o.docx"),
                "--engine", "local"])
    assert ret == 0 and "via hwp2pdf-cli" in capsys.readouterr().out


def test_cli_bad_engine_env(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HWP_AGENT_RENDER_ENGINE", "bogus")
    assert main(["pdf", str(_src(tmp_path))]) == 2
    assert "unknown render engine" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# verify records the engine
# --------------------------------------------------------------------------- #
def test_verify_hwp_records_hancom_engine(tmp_path: Path, fake_cli, monkeypatch) -> None:
    from hwp_agent.ops import verify as vmod
    from hwp_agent.ops.verify import PageVerdict, verify_hwp

    monkeypatch.setattr(vmod, "verify_pdf", lambda pdf, **k: vmod.VerifyResult(
        pdf=str(pdf), page_count=1, dpi=150, model="m", pages=[PageVerdict(1, True)]))
    r = verify_hwp(_src(tmp_path), vision_fn=lambda *a: PageVerdict(1, True))
    assert r.passed and r.engine == "hwp2pdf-cli" and r.as_dict()["engine"] == "hwp2pdf-cli"


def test_verify_hwp_rhwp_fallback_is_marked(tmp_path: Path, fake_cli, monkeypatch) -> None:
    from hwp_agent.ops import verify as vmod
    from hwp_agent.ops.verify import PageVerdict, verify_hwp

    monkeypatch.setenv("FAKE_H2P_MODE", "running")
    monkeypatch.setattr(vmod, "verify_pdf", lambda pdf, **k: vmod.VerifyResult(
        pdf=str(pdf), page_count=1, dpi=150, model="m", pages=[PageVerdict(1, True)]))
    monkeypatch.setattr("hwp_agent.render.select.LocalRhwpBackend",
                        lambda *a, **k: LocalRhwpBackend(render_fn=_fake_pdf))
    r = verify_hwp(_src(tmp_path), vision_fn=lambda *a: PageVerdict(1, True))
    assert r.engine == "rhwp" and "한글이 실행 중" in r.engine_note
