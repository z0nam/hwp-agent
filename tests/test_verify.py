"""Tests for the PDF render-quality verifier (Step 1, Hancom-independent).

The vision judge is injected (``vision_fn``) so these run with no API key or
network. PyMuPDF is required to build/rasterize the fixture PDFs; tests skip
cleanly if it is not installed.
"""

import importlib.util

import pytest

_HAS_FITZ = importlib.util.find_spec("fitz") is not None
pytestmark = pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")

from hwp_agent.ops.verify import (  # noqa: E402
    PageIssue,
    PageVerdict,
    verify_document,
    verify_hwp,
    verify_pdf,
)


def _make_pdf(path, pages=2):
    import fitz

    doc = fitz.open()
    for n in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {n + 1}")
    doc.save(str(path))
    doc.close()


def test_all_clean_passes(tmp_path):
    pdf = tmp_path / "ok.pdf"
    _make_pdf(pdf, 3)

    def judge(png, page, total):
        assert png[:8] == b"\x89PNG\r\n\x1a\n"  # a real PNG was rasterized
        assert total == 3
        return PageVerdict(page=page, ok=True)

    r = verify_pdf(pdf, vision_fn=judge)
    assert r.page_count == 3
    assert r.passed
    assert r.problem_pages == []


def test_problem_page_fails(tmp_path):
    pdf = tmp_path / "bad.pdf"
    _make_pdf(pdf, 3)

    def judge(png, page, total):
        if page == 2:
            return PageVerdict(
                page=page, ok=False, issues=[PageIssue("missing_image", "figure gone")]
            )
        return PageVerdict(page=page, ok=True)

    r = verify_pdf(pdf, vision_fn=judge)
    assert not r.passed
    assert r.problem_pages == [2]
    d = r.as_dict()
    assert d["problem_pages"] == [2]
    assert d["pages"][1]["issues"][0]["type"] == "missing_image"
    assert d["passed"] is False


def test_max_pages_limits_checks_but_reports_total(tmp_path):
    pdf = tmp_path / "many.pdf"
    _make_pdf(pdf, 5)
    seen = []

    def judge(png, page, total):
        seen.append(page)
        return PageVerdict(page=page, ok=True)

    r = verify_pdf(pdf, vision_fn=judge, max_pages=2)
    assert seen == [1, 2]
    assert r.page_count == 5  # total pages in the PDF
    assert r.as_dict()["checked"] == 2


def test_corrupt_pdf_is_guarded(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a valid pdf body")
    r = verify_pdf(bad, vision_fn=lambda *a: PageVerdict(1, True))
    assert not r.passed
    assert r.error


def test_empty_file_is_guarded(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    r = verify_pdf(empty, vision_fn=lambda *a: PageVerdict(1, True))
    assert not r.passed
    assert "empty" in r.error.lower()


def test_missing_file_is_guarded(tmp_path):
    r = verify_pdf(tmp_path / "nope.pdf", vision_fn=lambda *a: PageVerdict(1, True))
    assert not r.passed
    assert "not found" in r.error.lower()


def test_dpi_threads_through(tmp_path):
    pdf = tmp_path / "dpi.pdf"
    _make_pdf(pdf, 1)
    r = verify_pdf(pdf, dpi=72, vision_fn=lambda *a: PageVerdict(1, True))
    assert r.dpi == 72
    assert r.as_dict()["dpi"] == 72


# --------------------------------------------------------------------------- #
# .hwp/.hwpx path (rhwp render). Renderer injected → no rhwp binary needed.
# --------------------------------------------------------------------------- #
def test_verify_hwp_routes_through_renderer(tmp_path):
    src = tmp_path / "doc.hwpx"
    src.write_bytes(b"PK\x03\x04 fake hwpx")  # content irrelevant; renderer is faked

    def fake_render(s, out_pdf):
        assert s == src
        _make_pdf(out_pdf, 2)  # pretend rhwp produced a 2-page PDF

    r = verify_hwp(src, render_fn=fake_render, vision_fn=lambda *a: PageVerdict(1, True))
    assert r.passed
    assert r.page_count == 2
    assert r.pdf == str(src)  # report the source, not the temp render


def test_verify_hwp_render_failure_is_guarded(tmp_path):
    src = tmp_path / "doc.hwp"
    src.write_bytes(b"junk")

    def boom(s, out_pdf):
        raise RuntimeError("rhwp export-pdf failed: boom")

    r = verify_hwp(src, render_fn=boom, vision_fn=lambda *a: PageVerdict(1, True))
    assert not r.passed
    assert "render failed" in r.error


def test_verify_hwp_missing_rhwp_binary(tmp_path):
    src = tmp_path / "doc.hwpx"
    src.write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        verify_hwp(src, rhwp_bin="/nonexistent/rhwp-binary")  # no render_fn → resolve fails


def test_verify_document_dispatches_by_extension(tmp_path):
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, 1)
    r_pdf = verify_document(pdf, vision_fn=lambda *a: PageVerdict(1, True))
    assert r_pdf.passed and r_pdf.pdf == str(pdf)

    hwpx = tmp_path / "b.hwpx"
    hwpx.write_bytes(b"x")
    r_hwp = verify_document(
        hwpx, render_fn=lambda s, o: _make_pdf(o, 1),
        vision_fn=lambda *a: PageVerdict(1, True),
    )
    assert r_hwp.passed and r_hwp.pdf == str(hwpx)
