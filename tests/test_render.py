"""Tests for hwp_agent.render — all offline (injected render_fn / fake server).

No rhwp binary and no namun-ji are touched; the hwp2pdf round-trip runs against
the in-process fake hwp2pdf-serve from ``conftest.py`` (or an injected transport).
"""

from __future__ import annotations

import json
from pathlib import Path

from hwp_agent.cli.main import main
from hwp_agent.render import (
    Hwp2PdfClient,
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
# hwp2pdf-serve client (fake server from conftest)
# --------------------------------------------------------------------------- #
def _src(tmp_path: Path, name: str = "doc.hwpx") -> Path:
    p = tmp_path / name
    p.write_bytes(b"PK\x03\x04 fake hwpx")
    return p


def test_hwp2pdf_pdf_roundtrip(tmp_path: Path, fake_hwp2pdf) -> None:
    out = tmp_path / "o.pdf"
    r = RemoteHwp2PdfBackend(resolve_hwp2pdf_config()).render(_src(tmp_path), out, fmt="pdf")
    assert r.ok and r.backend == "hwp2pdf" and out.read_bytes().startswith(b"%PDF")
    assert fake_hwp2pdf.run_payloads[-1]["output_format"] == "PDF"
    assert fake_hwp2pdf.deleted == ["job1"]  # server job always cleaned up
    assert not (tmp_path / "o.pdf.part").exists()


def test_hwp2pdf_docx_only_requests_docx(tmp_path: Path, fake_hwp2pdf) -> None:
    out = tmp_path / "o.docx"
    r = RemoteHwp2PdfBackend(resolve_hwp2pdf_config()).render(_src(tmp_path), out, fmt="docx")
    assert r.ok
    assert [p["output_format"] for p in fake_hwp2pdf.run_payloads] == ["DOCX"]


def test_hwp2pdf_hwpx_resave_uploads_bare_stem(tmp_path: Path, fake_hwp2pdf) -> None:
    # the server rejects "*.hwpx -> HWPX" by name; Hangul detects HWPX by content
    fake_hwp2pdf.convert = lambda data, fmt: data
    out = tmp_path / "re.hwpx"
    Hwp2PdfClient(resolve_hwp2pdf_config()).convert(_src(tmp_path), out, "hwpx")
    assert fake_hwp2pdf.run_payloads[-1] == {
        "name": "doc", "output_format": "HWPX", "force_one_page": True,
    }
    assert out.is_file()


def test_hwp2pdf_skipped_when_hangul_running(tmp_path: Path, fake_hwp2pdf) -> None:
    fake_hwp2pdf.hwp_running = ["4242"]
    be = RemoteHwp2PdfBackend(resolve_hwp2pdf_config())
    st = be.status("pdf")
    assert not st.usable and st.busy and "한글이 실행 중" in st.reason
    r = be.render(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf")
    assert not r.ok and r.busy
    # never submitted a job while Hangul was open
    assert not any(path == "/v1/jobs" for _m, path in fake_hwp2pdf.requests)


def test_hwp2pdf_skipped_when_queue_busy(fake_hwp2pdf) -> None:
    fake_hwp2pdf.queue_depth = 2
    st = Hwp2PdfClient(resolve_hwp2pdf_config()).status()
    assert not st.usable and st.busy and "대기 2건" in st.reason


def test_hwp2pdf_bad_token(fake_hwp2pdf, monkeypatch) -> None:
    monkeypatch.setenv("HWP2PDF_TOKEN", "wrong")
    st = Hwp2PdfClient(resolve_hwp2pdf_config()).status()
    assert not st.usable and not st.busy and "인증 실패" in st.reason
    assert "wrong" not in st.reason  # token never echoed


def test_hwp2pdf_missing_token(fake_hwp2pdf, monkeypatch) -> None:
    monkeypatch.delenv("HWP2PDF_TOKEN")
    st = Hwp2PdfClient(resolve_hwp2pdf_config()).status()
    assert not st.usable and "토큰 없음" in st.reason


def test_hwp2pdf_token_file(tmp_path: Path, fake_hwp2pdf, monkeypatch) -> None:
    from conftest import TOKEN

    monkeypatch.delenv("HWP2PDF_TOKEN")
    tok = tmp_path / "token.txt"
    tok.write_text(TOKEN + "\n")
    cfg = tmp_path / "hwp2pdf.json"
    cfg.write_text(json.dumps({"server_url": fake_hwp2pdf.url, "token_file": str(tok)}))
    monkeypatch.delenv("HWP2PDF_SERVER_URL")
    assert Hwp2PdfClient(resolve_hwp2pdf_config(cfg)).status().usable


def test_hwp2pdf_unreachable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HWP2PDF_SERVER_URL", "http://127.0.0.1:9")  # discard port
    monkeypatch.setenv("HWP2PDF_TOKEN", "x")
    st = Hwp2PdfClient(resolve_hwp2pdf_config()).status()
    assert not st.usable and not st.busy and "연결 못 함" in st.reason


def test_hwp2pdf_item_failure_is_result(tmp_path: Path, fake_hwp2pdf) -> None:
    fake_hwp2pdf.fail_item = "한글 저장 실패"
    r = RemoteHwp2PdfBackend(resolve_hwp2pdf_config()).render(
        _src(tmp_path), tmp_path / "o.pdf", fmt="pdf"
    )
    assert r.returncode == 1 and "한글 저장 실패" in r.stderr
    assert fake_hwp2pdf.deleted == ["job1"]


def test_hwp2pdf_injected_transport_api_mismatch() -> None:
    def tx(method, url, *, headers, body, timeout):
        return 200, json.dumps({"api": 2, "auth_required": False}).encode()

    st = Hwp2PdfClient(Hwp2PdfConfig("http://x:1"), transport=tx).status()
    assert not st.usable and "프로토콜" in st.reason


# --------------------------------------------------------------------------- #
# Selection / fallback
# --------------------------------------------------------------------------- #
def test_select_auto_prefers_remote_when_usable(fake_hwp2pdf) -> None:
    be = select_render_backend("pdf", "auto")
    assert be.name == "hwp2pdf"


def test_select_auto_skips_server_without_the_format(fake_hwp2pdf) -> None:
    fake_hwp2pdf.formats = ["DOCX"]
    assert select_render_backend("pdf", "auto").name == "rhwp"


def test_hwp2pdf_whole_job_deadline_bounds_transfers(tmp_path: Path, fake_hwp2pdf) -> None:
    import dataclasses

    cfg = dataclasses.replace(resolve_hwp2pdf_config(), convert_timeout=0)
    r = RemoteHwp2PdfBackend(cfg).render(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf")
    assert not r.ok and "0초 안에" in r.stderr
    assert not any(m == "PUT" for m, _p in fake_hwp2pdf.requests)  # no upload started
    assert fake_hwp2pdf.deleted == ["job1"]


def test_select_auto_falls_to_rhwp_without_config() -> None:
    be = select_render_backend("pdf", "auto", config=None, _resolve=False)
    assert be.name == "rhwp"


def test_select_docx_always_remote() -> None:
    be = select_render_backend("docx", "auto", config=None, _resolve=False)
    assert be.name == "hwp2pdf"


def test_render_document_pdf_busy_falls_back_to_rhwp(tmp_path: Path, fake_hwp2pdf) -> None:
    fake_hwp2pdf.hwp_running = ["1"]
    out = tmp_path / "o.pdf"
    r = render_document(_src(tmp_path), out, fmt="pdf", engine="auto", render_fn=_fake_pdf)
    assert r.ok and r.backend == "rhwp"
    assert r.note.startswith("hwp2pdf 미사용") and "한글이 실행 중" in r.note


def test_render_document_docx_busy_has_no_fallback(tmp_path: Path, fake_hwp2pdf) -> None:
    fake_hwp2pdf.hwp_running = ["1"]
    r = render_document(_src(tmp_path), tmp_path / "o.docx", fmt="docx", engine="auto",
                        render_fn=_fake_pdf)
    assert not r.ok and r.busy and "한글이 실행 중" in r.stderr


def test_render_document_explicit_hwp2pdf_respects_overlap(tmp_path: Path, fake_hwp2pdf) -> None:
    fake_hwp2pdf.queue_depth = 1
    r = render_document(_src(tmp_path), tmp_path / "o.pdf", fmt="pdf", engine="hwp2pdf",
                        render_fn=_fake_pdf)
    assert not r.ok and r.backend == "hwp2pdf"


def test_render_document_docx_no_config_clean_error(tmp_path: Path) -> None:
    r = render_document(_src(tmp_path), tmp_path / "x.docx", fmt="docx", engine="auto")
    assert r.returncode == 2 and "hwp2pdf" in r.stderr.lower()


def test_cli_pdf_prints_fallback_note(tmp_path: Path, fake_hwp2pdf, capsys, monkeypatch) -> None:
    fake_hwp2pdf.hwp_running = ["1"]
    monkeypatch.setattr("hwp_agent.render.select.LocalRhwpBackend",
                        lambda *a, **k: LocalRhwpBackend(render_fn=_fake_pdf))
    ret = main(["pdf", str(_src(tmp_path)), "-o", str(tmp_path / "o.pdf")])
    cap = capsys.readouterr()
    assert ret == 0 and "via rhwp" in cap.out
    assert "rhwp 근사 렌더(미리보기용)" in cap.out  # never mistaken for Hangul's layout
    assert "note: hwp2pdf 미사용" in cap.err


# --------------------------------------------------------------------------- #
# Config resolution
# --------------------------------------------------------------------------- #
def test_config_none_when_absent(tmp_path: Path) -> None:
    assert resolve_hwp2pdf_config(tmp_path / "none.json") is None


def test_config_auto_local_when_unconfigured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HWP2PDF_NO_LOCAL")
    c = resolve_hwp2pdf_config(tmp_path / "none.json")
    assert c is not None and c.auto_local and c.server_url == "http://127.0.0.1:17650"


def test_auto_local_server_is_found_and_used(tmp_path: Path, fake_hwp2pdf, monkeypatch) -> None:
    # a researcher's Windows PC running "hwp2pdf serve": nothing configured, the
    # token is the one "serve --init" saved under %LOCALAPPDATA%
    from conftest import TOKEN

    monkeypatch.delenv("HWP2PDF_NO_LOCAL")
    monkeypatch.delenv("HWP2PDF_SERVER_URL")
    monkeypatch.delenv("HWP2PDF_TOKEN")
    monkeypatch.setattr("hwp_agent.render.config.LOCAL_URL", fake_hwp2pdf.url)
    appdata = tmp_path / "appdata"
    (appdata / "hwp2pdf").mkdir(parents=True)
    (appdata / "hwp2pdf" / "server_token.txt").write_text(TOKEN)
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    c = resolve_hwp2pdf_config(tmp_path / "none.json")
    assert c.auto_local and Hwp2PdfClient(c).status().usable


def test_auto_local_absent_reads_as_unconfigured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HWP2PDF_NO_LOCAL")
    monkeypatch.setattr("hwp_agent.render.config.LOCAL_URL", "http://127.0.0.1:9")
    st = Hwp2PdfClient(resolve_hwp2pdf_config(tmp_path / "none.json")).status()
    assert not st.usable and "미설정" in st.reason and "이 PC" in st.reason


def test_config_ignores_retired_ssh_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "hwp2pdf.json"
    cfg.write_text(json.dumps({"host": "namun-ji", "remote_inbox": "C:/in",
                               "remote_outbox": "C:/out", "task_name": "t1"}))
    assert resolve_hwp2pdf_config(cfg) is None


def test_config_file_and_env_override(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "hwp2pdf.json"
    cfg.write_text(json.dumps({"server_url": "namun-ji", "host": "ignored"}))
    c = resolve_hwp2pdf_config(cfg)
    assert c is not None and c.server_url == "http://namun-ji:17650"
    monkeypatch.setenv("HWP2PDF_SERVER_URL", "https://conv.example.com")
    monkeypatch.setenv("HWP2PDF_TIMEOUT", "60")
    c2 = resolve_hwp2pdf_config(cfg)
    assert c2.server_url == "https://conv.example.com" and c2.convert_timeout == 60
    assert "token" not in repr(c2).lower().replace("token_file", "")
