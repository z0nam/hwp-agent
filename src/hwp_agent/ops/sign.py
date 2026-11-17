"""Stamp a signature image onto a PDF at its "(인)" / "서명" marks (hwp-agent#88).

Forms that want "서명 후 스캔본" are usually printed, signed, and scanned. This does the
same on the PDF a form renders to: find the signature mark's text on the page, and draw
the (transparent-background) signature image centred on it, over the text — the mark
shows through the transparent pixels, as it would under real ink.

Only PyMuPDF is used (the ``verify`` extra): ``search_for`` gives the mark's box, and
``insert_image`` places the signature. The image is cropped to its ink in memory and
never written anywhere but the output PDF — a signature is close to biometric data, so
no temp file, cache, or log keeps a copy. A ``--preview`` PNG is written only to a path
the caller names.

An HWPX-native variant (signature as a picture object inside the document, issue #88's
original proposal) is a separate, later step; a PDF is what "서명본" submissions ask for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Tried in order; the first that occurs anywhere in the document wins. "(서명)" comes
# before "서명" because the latter also matches inside the former.
DEFAULT_ANCHORS: tuple[str, ...] = ("(인)", "(서명)", "서명")
DEFAULT_WIDTH = 72.0  # points (1 inch) — about the size of a hand signature on A4

# Without an alpha channel, pixels at least this light count as paper, not ink.
_PAPER = 235


@dataclass(frozen=True)
class Placement:
    page: int  # 1-based
    anchor: str
    anchor_rect: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points (top-left origin)
    image_rect: tuple[float, float, float, float]

    def as_dict(self) -> dict:
        return {
            "page": self.page,
            "anchor": self.anchor,
            "anchor_rect": [round(v, 1) for v in self.anchor_rect],
            "image_rect": [round(v, 1) for v in self.image_rect],
        }


@dataclass
class SignResult:
    output: Path | None
    placements: list[Placement] = field(default_factory=list)
    previews: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.placements)


def _ink_pixmap(image: Path | str):
    """The signature as an RGBA pixmap cropped to its ink.

    A PNG with transparency keeps its alpha. An opaque image (a scan or photo of a
    signature on white paper) gets an alpha mask that makes light pixels transparent,
    so the paper does not cover the form's text.
    """
    import fitz

    pix = fitz.Pixmap(str(image))
    if pix.colorspace is None or pix.colorspace.n != 3:  # gray / CMYK / indexed → RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha and not pix.samples[pix.n - 1::pix.n].strip(b"\xff"):
        pix = fitz.Pixmap(pix, 0)  # RGBA but fully opaque (white canvas): treat as a scan
    if not pix.alpha:
        gray = fitz.Pixmap(fitz.csGRAY, pix)
        paper = bytes(255 if v < _PAPER else 0 for v in range(256))
        mask = fitz.Pixmap(fitz.csGRAY, gray.width, gray.height,
                           gray.samples.translate(paper), False)
        pix = fitz.Pixmap(fitz.Pixmap(pix, 0), mask)  # drop alpha (none) + attach mask

    w, h, n, stride = pix.width, pix.height, pix.n, pix.stride
    samples = pix.samples
    top = bottom = None
    left, right = w, -1
    for y in range(h):
        alpha = samples[y * stride + n - 1:(y + 1) * stride:n]
        stripped = alpha.lstrip(b"\x00")
        if not stripped:
            continue
        if top is None:
            top = y
        bottom = y
        left = min(left, w - len(stripped))
        right = max(right, len(alpha.rstrip(b"\x00")) - 1)
    if top is None:
        raise ValueError(f"signature image has no visible pixels: {image}")
    if (left, top, right, bottom) == (0, 0, w - 1, h - 1):
        return pix
    cw, ch = right - left + 1, bottom - top + 1
    rows = b"".join(
        samples[y * stride + left * n:y * stride + (right + 1) * n] for y in range(top, bottom + 1)
    )
    return fitz.Pixmap(fitz.csRGB, cw, ch, rows, True)


def _page_chars(page) -> tuple[str, list]:
    """The page's non-space characters, in reading order, with each one's box."""
    import fitz

    text, boxes = [], []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                for ch in span["chars"]:
                    if not ch["c"].isspace():
                        text.append(ch["c"])
                        boxes.append(fitz.Rect(ch["bbox"]))
    return "".join(text), boxes


def _search(page, anchor: str) -> list:
    """Boxes of ``anchor`` on ``page``, ignoring whitespace.

    Plain ``search_for`` misses marks whose spacing the PDF writer changed — rhwp emits
    "(인)" as "( 인)", and a form may print "( 인 )" — so match on the characters alone.
    """
    import fitz

    needle = "".join(anchor.split())
    if not needle:
        return []
    text, boxes = _page_chars(page)
    hits, i = [], text.find(needle)
    while i >= 0:
        box = fitz.Rect(boxes[i])
        for b in boxes[i + 1:i + len(needle)]:
            box |= b
        hits.append(box)
        i = text.find(needle, i + len(needle))
    return hits


def find_anchors(doc, anchors: tuple[str, ...], page: int | None = None):
    """``(anchor, [(page_no, rect), ...])`` for the first anchor that occurs, else ``(None, [])``.

    Hits are in reading order — by page, then top to bottom, left to right — so "the
    last mark" is the one lowest on the last page that has one.
    """
    pages = [page] if page else range(1, doc.page_count + 1)
    for anchor in anchors:
        hits = [
            (p, r) for p in pages
            for r in sorted(_search(doc[p - 1], anchor), key=lambda r: (r.y0, r.x0))
        ]
        if hits:
            return anchor, hits
    return None, []


def sign_pdf(
    pdf: Path | str,
    out: Path | str | None,
    image: Path | str,
    *,
    anchors: tuple[str, ...] = DEFAULT_ANCHORS,
    width: float = DEFAULT_WIDTH,
    page: int | None = None,
    every: bool = False,
    dx: float = 0.0,
    dy: float = 0.0,
    preview: Path | str | None = None,
) -> SignResult:
    """Place ``image`` on ``pdf`` at the signature mark and save to ``out``.

    The last mark in the document is signed (the signer's line is normally at the end),
    or every mark with ``every``. ``page`` (1-based) limits the search to one page.
    ``dx``/``dy`` nudge the image in points (right/down). ``out=None`` is a dry run:
    nothing is written except the preview, if asked for.
    """
    import fitz

    result = SignResult(output=Path(out) if out else None)
    doc = fitz.open(str(pdf))
    try:
        if page is not None and not 1 <= page <= doc.page_count:
            raise ValueError(f"--page {page} out of range (1-{doc.page_count})")
        anchor, hits = find_anchors(doc, anchors, page)
        if not hits:
            where = f" on page {page}" if page else ""
            result.warnings.append(
                f"no signature mark found{where} (looked for: {', '.join(anchors)})"
            )
            return result
        if not every:
            hits = hits[-1:]

        ink = _ink_pixmap(image)
        height = width * ink.height / ink.width
        for page_no, r in hits:
            cx, cy = (r.x0 + r.x1) / 2 + dx, (r.y0 + r.y1) / 2 + dy
            box = fitz.Rect(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
            doc[page_no - 1].insert_image(box, pixmap=ink, overlay=True)
            result.placements.append(
                Placement(page_no, anchor, tuple(r), tuple(box))  # type: ignore[arg-type]
            )

        if out:  # before the preview, so a preview problem never costs the signed PDF
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(out), garbage=3, deflate=True)
        if preview:
            result.previews = _write_previews(doc, result.placements, Path(preview))
    finally:
        doc.close()
    return result


def _write_previews(doc, placements: list[Placement], dest: Path) -> list[Path]:
    """A 2x PNG of each signed spot with some surrounding text, to check the placement."""
    import fitz

    paths = []
    for i, pl in enumerate(placements, 1):
        page = doc[pl.page - 1]
        box = fitz.Rect(pl.image_rect) | fitz.Rect(pl.anchor_rect)
        # Placements are in unrotated page space; the render clip is in the rotated
        # (displayed) space, like page.rect.
        box = box * page.rotation_matrix
        clip = fitz.Rect(box.x0 - 140, box.y0 - 40, box.x1 + 60, box.y1 + 40) & page.rect
        path = dest if len(placements) == 1 else dest.with_name(f"{dest.stem}-{i}{dest.suffix}")
        path.parent.mkdir(parents=True, exist_ok=True)
        page.get_pixmap(clip=clip, dpi=144).save(str(path))
        paths.append(path)
    return paths
