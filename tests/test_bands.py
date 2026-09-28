"""Tests for hwp_agent.ops.bands (section-band detection — issue #10)."""

from __future__ import annotations

from pathlib import Path

from hwp_agent.ops.bands import (
    bands_from_page_hashes,
    detect_document_bands,
    detect_section_bands,
)


# --------------------------------------------------------------------------- #
# pure grouping core
# --------------------------------------------------------------------------- #
def test_bands_split_where_background_changes() -> None:
    """Each section's background recurs; the change point is a band boundary."""
    # bg 'A' on pages 1-2, 'B' on 3-5, 'C' on 6 (with a one-off figure 'fig' on p4)
    pages = [
        {"A"}, {"A"},
        {"B"}, {"B", "fig"}, {"B"},
        {"C"}, {"C"},
    ]
    bands, decidable = bands_from_page_hashes(pages)
    assert decidable is True
    spans = [(b.start_page, b.end_page, b.band_id) for b in bands]
    assert spans == [(1, 2, "A"), (3, 5, "B"), (6, 7, "C")]
    # the one-off figure on p4 did not split band B
    assert bands[1].recurring_images == 1


def test_oneoff_images_are_ignored() -> None:
    """A background 'bg' on every page + unique figures per page → one band."""
    pages = [{"bg", "f1"}, {"bg", "f2"}, {"bg", "f3"}]
    bands, decidable = bands_from_page_hashes(pages)
    assert decidable is False  # only one signature ({bg}) → no boundaries
    assert len(bands) == 1 and bands[0].start_page == 1 and bands[0].end_page == 3


def test_no_images_is_undecidable() -> None:
    pages = [set(), set(), set()]
    bands, decidable = bands_from_page_hashes(pages)
    assert decidable is False
    assert bands == [type(bands[0])(1, 3, "—", 0)]  # no background → "—"


def test_reused_background_shares_band_id() -> None:
    """A background reused in two non-consecutive sections keeps the same id."""
    # each bg recurs (≥2) so none is filtered; B is a real middle section
    pages = [{"A"}, {"A"}, {"B"}, {"B"}, {"A"}, {"A"}]
    bands, decidable = bands_from_page_hashes(pages)
    assert decidable is True
    ids = [b.band_id for b in bands]
    assert ids == ["A", "B", "A"]  # same signature → same letter


def test_facing_page_mirror_is_one_band() -> None:
    """X,Y,X,Y,… facing-page 쪽날개 mirror collapses to a single band (issue #10)."""
    # cover(83, 1×→filtered), then a 5-page mirror 70/1d, then body(unique→filtered)
    pages = [{"83"}, {"70"}, {"1d"}, {"70"}, {"1d"}, {"70"}, {"z1"}, {"z2"}, {"z3"}]
    bands, decidable = bands_from_page_hashes(pages)
    spans = [(b.start_page, b.end_page, b.recurring_images) for b in bands]
    # p1 (bg filtered) empty band, p2-6 mirror (2 bgs), p7-9 empty band
    assert (2, 6, 2) in spans
    assert decidable is True
    # a lone A,X,A interjection must NOT be treated as a mirror
    pages2 = [{"A"}, {"A"}, {"B"}, {"A"}, {"A"}]  # B is 1× → filtered to "—"
    bands2, _ = bands_from_page_hashes(pages2)
    assert [b.band_id for b in bands2] == ["A", "—", "A"]  # 3 separate bands, not merged


def test_empty_document() -> None:
    bands, decidable = bands_from_page_hashes([])
    assert bands == [] and decidable is False


# --------------------------------------------------------------------------- #
# detect_section_bands / detect_document_bands with injected page hashes
# --------------------------------------------------------------------------- #
def test_detect_section_bands_injected() -> None:
    def fake(_p):
        return [{"A"}, {"A"}, {"B"}]

    r = detect_section_bands("ignored.pdf", page_hashes_fn=fake)
    assert r.error is None and r.page_count == 3 and r.decidable is True
    assert [(b.start_page, b.end_page) for b in r.bands] == [(1, 2), (3, 3)]


def test_detect_document_bands_injected_skips_render(tmp_path: Path) -> None:
    # a .hwpx path but an injected page_hashes_fn → no render/rhwp needed
    def fake(_p):
        return [{"cover"}, {"body"}, {"body"}]

    r = detect_document_bands(tmp_path / "x.hwpx", page_hashes_fn=fake)
    assert r.error is None and r.decidable is True
    # cover(1×) filtered → "—"; body(2×) recurs → "A"
    assert r.bands[0].band_id == "—" and r.bands[-1].band_id == "A"


def test_missing_file_is_guarded() -> None:
    r = detect_section_bands("/no/such/file.pdf")
    assert r.error is not None and "not found" in r.error
