"""Detect section bands from a rendered document's per-page background images.

JI report templates give each part (표지·목차·요약·본문·참고문헌·판권) its own
**쪽날개 background image**, repeated on every page of that part. So the page at
which the background changes *is* a section boundary. Hashing the background per
page and grouping consecutive equal hashes surfaces those bands — a cheap,
**deterministic** check (no vision model) that catches ``write`` / ``text insert``
putting content in the wrong section (issue #10; the class of bug #6 only warns
about).

The trick is isolating the *background* from *content*: a page's inline figures
are one-offs, but a section background recurs across the section's pages. So the
per-page signature is the set of image hashes that **recur** (appear on ≥ 2 pages)
— figures drop out, backgrounds stay, and the signature changes exactly where the
background changes. A template with no background images collapses to a single
band → reported as *undecidable*.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: per-page signature = frozenset of recurring image content-hashes
PageHashesFn = Callable[[Path], list[set[str]]]


@dataclass(frozen=True)
class Band:
    start_page: int  # 1-based, inclusive
    end_page: int  # 1-based, inclusive
    band_id: str  # A, B, C … (same background → same id)
    recurring_images: int  # size of the recurring-image signature

    def as_dict(self) -> dict:
        return {
            "start_page": self.start_page,
            "end_page": self.end_page,
            "band_id": self.band_id,
            "recurring_images": self.recurring_images,
        }


@dataclass
class BandReport:
    source: str
    page_count: int
    bands: list[Band]
    decidable: bool  # False when every page shares one signature (no bands)
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "page_count": self.page_count,
            "decidable": self.decidable,
            "bands": [b.as_dict() for b in self.bands],
            "error": self.error,
        }


def _letter(n: int) -> str:
    """0→A … 25→Z, 26→AA … (spreadsheet-style, for readable band ids)."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


#: a mirror (left/right facing-page) background alternation must run at least this
#: long to be merged — long enough to distinguish a real ``X,Y,X,Y`` page-wing
#: mirror from a lone one-page interjection (``A,X,A``).
_MIRROR_MIN_LEN = 4


def _mirror_runs(sigs: list) -> list[tuple[int, int]]:
    """Maximal runs [s,e] that strictly alternate between exactly 2 signatures.

    JI templates mirror the 쪽날개 on facing pages, so one section shows up as
    ``X,Y,X,Y,…`` — two recurring backgrounds alternating. Such a run (length
    ≥ :data:`_MIRROR_MIN_LEN`) is one section, not many.
    """
    n = len(sigs)
    runs: list[tuple[int, int]] = []
    i = 0
    while i < n - 1:
        if sigs[i + 1] == sigs[i]:
            i += 1
            continue
        # a candidate alternation starts at i between values sigs[i], sigs[i+1]
        a, b = sigs[i], sigs[i + 1]
        j = i + 1
        while j + 1 < n and sigs[j + 1] != sigs[j] and sigs[j + 1] in (a, b):
            j += 1
        if j - i + 1 >= _MIRROR_MIN_LEN:
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def bands_from_page_hashes(page_hashes: list[set[str]]) -> tuple[list[Band], bool]:
    """Pure core: per-page image-hash sets → (bands, decidable).

    A hash that appears on ≥ 2 pages is a *recurring* (background) image; one-off
    figures are ignored. Consecutive pages with the same recurring-hash set form a
    band; a facing-page **mirror alternation** (``X,Y,X,Y,…``) is merged into one
    band. ``decidable`` is False when there is only one distinct band signature (a
    flat document with no per-section backgrounds).
    """
    n = len(page_hashes)
    if n == 0:
        return [], False

    counts: Counter[str] = Counter()
    for hs in page_hashes:
        for h in hs:
            counts[h] += 1
    recurring = {h for h, c in counts.items() if c >= 2}
    sigs = [frozenset(hs & recurring) for hs in page_hashes]

    # pages covered by a mirror alternation → single band with the 2-sig union
    in_mirror = [False] * n
    mirror_of: dict[int, tuple[int, int]] = {}
    for s, e in _mirror_runs(sigs):
        for k in range(s, e + 1):
            in_mirror[k] = True
            mirror_of[k] = (s, e)

    sig_id: dict[frozenset, str] = {}

    def _id(sig: frozenset) -> str:
        # a band with no recurring background can't be identified — mark it "—"
        # rather than a letter, so it isn't confused with a real background band.
        if not sig:
            return "—"
        if sig not in sig_id:
            sig_id[sig] = _letter(len(sig_id))
        return sig_id[sig]

    bands: list[Band] = []
    i = 0
    while i < n:
        if in_mirror[i]:
            s, e = mirror_of[i]
            union = frozenset().union(*sigs[s : e + 1])
            bands.append(Band(s + 1, e + 1, _id(union), len(union)))
            i = e + 1
            continue
        # a plain run of equal signatures (not part of a mirror)
        start = i
        while i + 1 < n and not in_mirror[i + 1] and sigs[i + 1] == sigs[start]:
            i += 1
        bands.append(Band(start + 1, i + 1, _id(sigs[start]), len(sigs[start])))
        i += 1

    # boundaries are visible when more than one band emerged AND at least one has
    # an identifiable background (all-"—" means no per-section backgrounds at all).
    decidable = len(bands) > 1 and len(sig_id) >= 1
    return bands, decidable


def _page_image_hashes_fitz(pdf_path: Path) -> list[set[str]]:
    """Per-page set of image content-hashes, via PyMuPDF (lazy import)."""
    import fitz  # PyMuPDF — optional dependency

    out: list[set[str]] = []
    with fitz.open(pdf_path) as doc:
        for pno in range(doc.page_count):
            hashes: set[str] = set()
            for img in doc.get_page_images(pno, full=True):
                xref = img[0]
                try:
                    data = doc.extract_image(xref)["image"]
                except Exception:  # noqa: BLE001 — skip an unreadable image
                    continue
                hashes.add(hashlib.sha1(data).hexdigest())  # noqa: S324 (not security)
            out.append(hashes)
    return out


def detect_section_bands(
    pdf_path: str | Path, *, page_hashes_fn: PageHashesFn | None = None
) -> BandReport:
    """Group a PDF's pages into section bands by recurring background image.

    ``page_hashes_fn`` is injectable for tests (defaults to the PyMuPDF reader).
    Guard failures (missing/corrupt PDF) set ``error`` instead of raising.
    """
    pdf_path = Path(pdf_path)
    report = BandReport(source=str(pdf_path), page_count=0, bands=[], decidable=False)
    if page_hashes_fn is None and not pdf_path.is_file():
        report.error = f"file not found: {pdf_path}"
        return report

    fn = page_hashes_fn or _page_image_hashes_fitz
    try:
        page_hashes = fn(pdf_path)
    except ModuleNotFoundError:
        raise  # surfaced by the CLI as an install hint
    except Exception as exc:  # noqa: BLE001 — any open/decode failure is a guard fail
        report.error = f"could not read PDF images (corrupt?): {exc}"
        return report

    report.page_count = len(page_hashes)
    report.bands, report.decidable = bands_from_page_hashes(page_hashes)
    return report


def detect_document_bands(
    path: str | Path,
    *,
    page_hashes_fn: PageHashesFn | None = None,
    rhwp_bin: str | None = None,
    render_fn=None,
) -> BandReport:
    """Detect bands for any input: ``.hwp``/``.hwpx`` render to PDF (rhwp) first.

    Mirrors :func:`hwp_agent.ops.verify.verify_hwp`'s render step. ``render_fn`` is
    injectable for tests; when a ``page_hashes_fn`` is injected the render is
    skipped entirely (the fake supplies page hashes directly).
    """
    import tempfile

    path = Path(path)
    if page_hashes_fn is not None or path.suffix.lower() not in (".hwp", ".hwpx"):
        return detect_section_bands(path, page_hashes_fn=page_hashes_fn)

    if not path.is_file():
        return BandReport(str(path), 0, [], False, error=f"input not found: {path}")

    from ..render.rhwp import resolve_rhwp, rhwp_render_fn

    render = render_fn
    if render is None:
        rb = resolve_rhwp(rhwp_bin)
        if rb is None:
            raise FileNotFoundError(
                "rhwp not found — install the rhwp CLI on PATH or set RHWP_BIN "
                "(https://github.com/edwardkim/rhwp/releases)."
            )
        render = rhwp_render_fn(rb)

    with tempfile.TemporaryDirectory(prefix="hwp_bands_") as td:
        out_pdf = Path(td) / "rendered.pdf"
        try:
            render(path, out_pdf)
        except Exception as exc:  # noqa: BLE001 — render failure is a guard fail
            return BandReport(str(path), 0, [], False, error=f"render failed: {exc}")
        report = detect_section_bands(out_pdf)
    report.source = str(path)  # report the source, not the temp render
    return report
