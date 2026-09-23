"""Detect and drop stale ``<hp:linesegarray>`` line-layout caches (issue #15).

A ``<hp:linesegarray>`` is Hangul's per-paragraph line-layout cache — one
``<hp:lineseg>`` per visual line, recorded at save time. If a paragraph is edited
so it gains lines (e.g. an inserted ``<hp:lineBreak/>``) but the stale cache keeps
its old, smaller ``lineseg`` count, **both Hangul and rhwp draw the extra lines
piled onto one** (they trust the cache). The structural tell is cheap:
``lineBreak count + 1 > lineseg count`` means the cache is definitely too small.

Two entry points:

* :func:`iter_mismatches` — read-only; powers ``hwp-agent check``.
* :func:`drop_stale_linesegarrays` — removes the cache from mismatched paragraphs
  so Hangul recomputes it on open (it opens fine without one); powers
  ``hwp-agent normalize --drop-linesegarray``.

Both count **direct children only** and walk every ``<hp:p>`` (including table-cell
paragraphs, where issue #15 was first seen), so each paragraph is judged on its own
runs — never on a nested table's content.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .container import _rewrite_zip_preserving

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


@dataclass(frozen=True)
class LinesegMismatch:
    section: str  # e.g. "Contents/section0.xml"
    index: int  # position of the <hp:p> in document order within its section
    linebreaks: int
    linesegs: int
    text: str  # short preview of the paragraph's text

    def as_dict(self) -> dict:
        return {
            "section": self.section,
            "index": self.index,
            "linebreaks": self.linebreaks,
            "linesegs": self.linesegs,
            "text": self.text,
        }


def _direct_linebreaks(p) -> int:
    """Count ``<hp:lineBreak/>`` in this paragraph's own runs (not nested tables)."""
    return len(p.findall(f"{_hp('run')}/{_hp('t')}/{_hp('lineBreak')}"))


def _direct_linesegs(p) -> int:
    """Count ``<hp:lineseg>`` in this paragraph's own ``<hp:linesegarray>``."""
    return len(p.findall(f"{_hp('linesegarray')}/{_hp('lineseg')}"))


def _para_text(p) -> str:
    return "".join(t.text or "" for t in p.findall(f"{_hp('run')}/{_hp('t')}"))


def _is_stale(nlb: int, nseg: int) -> bool:
    """True when a present cache is provably too small for the paragraph's lines."""
    # nseg == 0 means no cache at all -> Hangul recomputes, so it's never stale.
    return nseg >= 1 and (nlb + 1) > nseg


def _section_parts(names: list[str]) -> list[str]:
    import re

    return sorted(n for n in names if re.search(r"Contents/section\d+\.xml$", n))


def iter_mismatches(path: str | Path) -> list[LinesegMismatch]:
    """Return every paragraph whose ``<hp:linesegarray>`` is provably too small."""
    out: list[LinesegMismatch] = []
    with zipfile.ZipFile(str(path)) as z:
        for part in _section_parts(z.namelist()):
            root = etree.fromstring(z.read(part))
            for i, p in enumerate(root.iter(_hp("p"))):
                nseg = _direct_linesegs(p)
                nlb = _direct_linebreaks(p)
                if _is_stale(nlb, nseg):
                    out.append(
                        LinesegMismatch(part, i, nlb, nseg, _para_text(p)[:40])
                    )
    return out


def drop_stale_linesegarrays(src: str | Path, dst: str | Path) -> int:
    """Remove the ``<hp:linesegarray>`` from every stale paragraph (issue #15).

    Container-preserving (no Hangul 보안경고): only the sections that actually
    change are rewritten, each with its original ZipInfo. Hangul rebuilds the
    dropped cache on open. Returns the number of paragraphs fixed.
    """
    import shutil

    src, dst = Path(src), Path(dst)
    overrides: dict[str, bytes] = {}
    fixed = 0
    with zipfile.ZipFile(str(src)) as z:
        parts = _section_parts(z.namelist())
        blobs = {part: z.read(part) for part in parts}
    for part, blob in blobs.items():
        root = etree.fromstring(blob)
        changed = 0
        for p in root.iter(_hp("p")):
            if not _is_stale(_direct_linebreaks(p), _direct_linesegs(p)):
                continue
            lsa = p.find(_hp("linesegarray"))
            if lsa is not None:
                p.remove(lsa)
                changed += 1
        if changed:
            decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            overrides[part] = decl + etree.tostring(
                root, encoding="UTF-8", xml_declaration=False
            )
            fixed += changed
    if overrides:
        # write via a temp so src == dst (in-place) is safe — can't read and
        # rewrite the same zip at once.
        tmp = dst.with_suffix(dst.suffix + ".lsdrop.tmp")
        _rewrite_zip_preserving(src, tmp, overrides)
        tmp.replace(dst)
    elif str(src) != str(dst):
        # nothing stale — still honor an explicit output path (byte-for-byte copy)
        shutil.copyfile(src, dst)
    return fixed
