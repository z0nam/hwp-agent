"""Tests for hwp_agent.render — all offline (injected render_fn / fake transport).

No rhwp binary and no namun-ji are touched; the remote round-trip is exercised
against a fake transport backed by tmp_path inbox/outbox dirs.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from hwp_agent.render import (
    Hwp2PdfConfig,
    LocalRhwpBackend,
    RemoteHwp2PdfBackend,
    render_document,
    resolve_hwp2pdf_config,
    select_render_backend,
)


# --------------------------------------------------------------------------- #
# Tier 1 — LocalRhwpBackend (injected renderer)
# --------------------------------------------------------------------------- #
def _fake_pdf(_src, out) -> None:
    Path(out).write_bytes(b"%PDF-1.4\n%%EOF\n")


def test_local_rhwp_pdf_ok(tmp_path: Path) -> None:
    be = LocalRhwpBackend(render_fn=_fake_pdf)
    assert be.is_available()
    out = tmp_path / "o.pdf"
    r = be.render(tmp_path / "x.hwpx", out, fmt="pdf")
    assert r.ok and r.backend == "rhwp" and out.is_file()


def test_local_rhwp_docx_unsupported(tmp_path: Path) -> None:
    be = LocalRhwpBackend(render_fn=_fake_pdf)
    r = be.render(tmp_path / "x.hwpx", tmp_path / "o.docx", fmt="docx")
    assert r.returncode == 2 and not r.ok
    assert "docx" in r.stderr.lower()


def test_local_rhwp_render_failure_is_result_not_raise(tmp_path: Path) -> None:
    def boom(_s, _o):
        raise RuntimeError("rhwp export-pdf failed: boom")

    r = LocalRhwpBackend(render_fn=boom).render(
        tmp_path / "x.hwpx", tmp_path / "o.pdf", fmt="pdf"
    )
    assert r.returncode == 1 and "boom" in r.stderr


# --------------------------------------------------------------------------- #
# Fake remote transport backed by tmp_path inbox/outbox
# --------------------------------------------------------------------------- #
class FakeTransport:
    """Simulates namun-ji: a scp push to inbox, a schtasks trigger that
    synthesizes <job>.<fmt> + <job>.done in outbox, Test-Path polling, scp pull."""

    def __init__(self, inbox: Path, outbox: Path, *, produce: str | None = "pdf",
                 fail: bool = False) -> None:
        self.inbox, self.outbox = inbox, outbox
        self.produce = produce  # which output ext the worker makes; None = never done
        self.fail = fail
        self.cleaned = False

    def push(self, local: Path, remote: str) -> None:
        (self.inbox / Path(remote).name).write_bytes(Path(local).read_bytes())

    def pull(self, remote: str, local: Path) -> None:
        data = (self.outbox / Path(remote).name).read_bytes()
        Path(local).write_bytes(data)

    def run(self, argv, *, timeout):  # noqa: ARG002
        script = _decode(argv)
        if "schtasks /run" in script:
            # worker: for each inbox file, drop product + marker
            for f in self.inbox.iterdir():
                job = f.stem
                if self.fail:
                    (self.outbox / f"{job}.err").write_text("boom on hancom")
                elif self.produce:
                    (self.outbox / f"{job}.{self.produce}").write_bytes(b"%PDF-1.4")
                    (self.outbox / f"{job}.done").write_text("")
            return _cp(0, "")
        if "Test-Path" in script:
            m = re.search(r'Test-Path "([^"]+)\.done"', script)
            base = self.outbox / (Path(m.group(1)).name)
            if base.with_suffix(".done").exists():
                return _cp(0, "done")
            if base.with_suffix(".err").exists():
                return _cp(0, "err")
            return _cp(0, "wait")
        if "Get-Content" in script:
            m = re.search(r'Get-Content "([^"]+)"', script)
            return _cp(0, (self.outbox / Path(m.group(1)).name).read_text())
        if "Remove-Item" in script:
            self.cleaned = True
            return _cp(0, "")
        return _cp(0, "ok")  # echo probe


def _cp(rc: int, out: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr="")


def _decode(argv: list[str]) -> str:
    import base64
    if "-EncodedCommand" in argv:
        b64 = argv[argv.index("-EncodedCommand") + 1]
        return base64.b64decode(b64).decode("utf-16-le")
    return " ".join(argv)


def _cfg(tmp: Path) -> Hwp2PdfConfig:
    return Hwp2PdfConfig(
        host="fake", remote_inbox=str(tmp / "in"), remote_outbox=str(tmp / "out"),
        convert_timeout=5, poll_interval=0.0,
    )


# --------------------------------------------------------------------------- #
# Tier 2 — RemoteHwp2PdfBackend round-trip (fake transport)
# --------------------------------------------------------------------------- #
def test_remote_roundtrip_pdf(tmp_path: Path) -> None:
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    tx = FakeTransport(inbox, outbox, produce="pdf")
    be = RemoteHwp2PdfBackend(_cfg(tmp_path), transport=tx)
    src = tmp_path / "doc.hwpx"
    src.write_bytes(b"x")
    out = tmp_path / "doc.pdf"
    r = be.render(src, out, fmt="pdf")
    assert r.ok and r.remote is True and r.backend == "hwp2pdf"
    assert out.is_file() and tx.cleaned


def test_remote_docx_supported(tmp_path: Path) -> None:
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    tx = FakeTransport(inbox, outbox, produce="docx")
    be = RemoteHwp2PdfBackend(_cfg(tmp_path), transport=tx)
    src = tmp_path / "doc.hwpx"
    src.write_bytes(b"x")
    r = be.render(src, tmp_path / "doc.docx", fmt="docx")
    assert r.ok and r.remote is True


def test_remote_timeout_returns_error(tmp_path: Path) -> None:
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    tx = FakeTransport(inbox, outbox, produce=None)  # worker never finishes
    be = RemoteHwp2PdfBackend(_cfg(tmp_path), transport=tx)
    src = tmp_path / "doc.hwpx"
    src.write_bytes(b"x")
    r = be.render(src, tmp_path / "doc.pdf", fmt="pdf")
    assert not r.ok and r.returncode == 1
    assert "session 1" in r.stderr and "no output within" in r.stderr


def test_remote_worker_error_marker(tmp_path: Path) -> None:
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    tx = FakeTransport(inbox, outbox, fail=True)
    be = RemoteHwp2PdfBackend(_cfg(tmp_path), transport=tx)
    src = tmp_path / "doc.hwpx"
    src.write_bytes(b"x")
    r = be.render(src, tmp_path / "doc.pdf", fmt="pdf")
    assert not r.ok and "hwp2pdf failed" in r.stderr


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def test_select_auto_prefers_remote_when_available(tmp_path: Path) -> None:
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir()
    outbox.mkdir()
    tx = FakeTransport(inbox, outbox)
    be = select_render_backend("pdf", "auto", config=_cfg(tmp_path), transport=tx)
    assert be.name == "hwp2pdf"


def test_select_auto_falls_to_rhwp_without_config() -> None:
    be = select_render_backend("pdf", "auto", config=None, _resolve=False)
    assert be.name == "rhwp"


def test_select_docx_always_remote() -> None:
    be = select_render_backend("docx", "auto", config=None, _resolve=False)
    assert be.name == "hwp2pdf"


def test_render_document_docx_no_config_clean_error() -> None:
    r = render_document("x.hwpx", "/tmp/x.docx", fmt="docx", engine="auto",
                        config=None)
    assert r.returncode == 2 and "hwp2pdf" in r.stderr.lower()


# --------------------------------------------------------------------------- #
# Config resolution
# --------------------------------------------------------------------------- #
def test_config_none_when_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HWP2PDF_CONFIG", raising=False)
    for e in ("HWP2PDF_HOST", "HWP2PDF_INBOX", "HWP2PDF_OUTBOX"):
        monkeypatch.delenv(e, raising=False)
    assert resolve_hwp2pdf_config(tmp_path / "none.json") is None


def test_config_file_and_env_override(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "hwp2pdf.json"
    cfg.write_text(json.dumps({
        "host": "namun-ji", "remote_inbox": "C:/in", "remote_outbox": "C:/out",
        "exe_path": "ignored", "task_name": "t1",
    }))
    monkeypatch.delenv("HWP2PDF_HOST", raising=False)
    c = resolve_hwp2pdf_config(cfg)
    assert c is not None and c.host == "namun-ji" and c.task_name == "t1"
    monkeypatch.setenv("HWP2PDF_HOST", "other-node")
    c2 = resolve_hwp2pdf_config(cfg)
    assert c2.host == "other-node"  # env overrides file
