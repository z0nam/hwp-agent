"""Background pictures of the JI 과제 report forms, without the sample report (#95).

The 정책·전략·기반·센터과제 forms draw their page furniture as full-page JPGs on the
master pages. Two of them carry the worked example's **report title** as pixels
down the left margin of even pages, and the body's odd-page picture carries its
**sample chapter title** down the right margin. Text fixes (form fill, #120/#122)
never reach pixels, so every document built from these forms printed the sample.

* :func:`clean_form_backgrounds` swaps those pictures, recognised by the SHA-256
  of the form's original bytes, for copies with the lettering painted over in the
  surrounding flat colour (``assets/form-backgrounds/``; ``map.json`` maps original
  hash → clean file). Every other part is copied byte-for-byte.
* :func:`add_running_title_slot` puts the report title back as text: a
  ``{{running_title}}`` 글맵시 turned to read top to bottom where the picture's title
  was, on each even master page showing a cleaned picture. ``report_meta`` fills it
  (``running_title`` ← meta, else the title) and :func:`fit_running_titles` sizes it
  to the filled text. The odd-page chapter title
  is :mod:`.chapter_header`'s job; with the sample erased, a document that skips
  it shows no chapter title rather than the sample's.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from functools import cache
from pathlib import Path

from lxml import etree

from .chapter_header import (
    _HC,
    TITLE_ART_COMMENT,
    WingSpec,
    _char_look,
    _hh,
    _hp,
    _serialize,
    _title_art,
    fit_title_art,
)
from .container import _rewrite_zip_preserving

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "form-backgrounds"
SLOT = "{{running_title}}"
#: cleaned pictures that had the report title on the left margin (even pages)
_EVEN_TITLE = {"even-white.jpg", "even-gray.jpg"}


@cache
def _map() -> dict[str, str]:
    return json.loads((ASSET_DIR / "map.json").read_text(encoding="utf-8"))


@cache
def _clean_hashes() -> dict[str, str]:
    """SHA-256 of each cleaned asset → its file name."""
    return {
        hashlib.sha256((ASSET_DIR / name).read_bytes()).hexdigest(): name
        for name in _map().values()
    }


def clean_form_backgrounds(path: str | Path, output: str | Path | None = None) -> list[str]:
    """Replace known sample-lettered background pictures; returns the BinData parts swapped.

    In place unless *output* is given. A file with nothing to swap is left as is
    (copied to *output* if one is given).
    """
    src = Path(path)
    dst = Path(output) if output else src
    overrides: dict[str, bytes] = {}
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            if not name.startswith("BinData/"):
                continue
            clean = _map().get(hashlib.sha256(z.read(name)).hexdigest())
            if clean:
                overrides[name] = (ASSET_DIR / clean).read_bytes()
    if overrides:
        tmp = dst.with_name(dst.name + ".bg.tmp")
        _rewrite_zip_preserving(src, tmp, overrides)
        tmp.replace(dst)
    elif dst != src:
        dst.write_bytes(src.read_bytes())
    return sorted(overrides)


def _title_box(shape_id: int, look: tuple[str, str, bool], spec: WingSpec) -> str:
    """The ``{{running_title}}`` 글맵시 mirroring the odd-page wing on the left margin,
    turned to read top to bottom like the picture's title.

    Master-page objects are placed on the paper (unlike header-hosted ones), so the
    offsets are plain paper coordinates. Measured on the even pictures (2244×3071 px
    over 53858×73700 HWPUNIT): the title glyphs span x 148-176 px, from y 354 px.
    """
    face, color, bold = look
    return _title_art(
        text=SLOT, shape_id=shape_id, angle=90, em=spec.char_height,
        horz=spec.right_to_center - spec.char_height // 2,  # same distance from the left
        vert=spec.top + spec.text_inset, face=face, color=color, bold=bold,
    )


def fit_running_titles(path: str | Path) -> int:
    """Size each title 글맵시 on the master pages to the text now in it.

    Run after the slot is filled (:mod:`.report_meta`): the box was sized for the
    ``{{running_title}}`` token and a 글맵시 stretches its text to fill the box. One
    left empty is removed. Returns how many were changed.
    """
    path = Path(path)
    overrides: dict[str, bytes] = {}
    count = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not (name.startswith("Contents/masterpage") and name.endswith(".xml")):
                continue
            raw = z.read(name)
            if TITLE_ART_COMMENT.encode() not in raw:
                continue
            root = etree.fromstring(raw)
            for art in list(root.iter(_hp("textart"))):
                comment = art.find(_hp("shapeComment"))
                if comment is None or comment.text != TITLE_ART_COMMENT:
                    continue
                text = art.get("text", "")
                if SLOT in text:
                    continue
                if text.strip():
                    fit_title_art(art, text)
                else:
                    art.getparent().remove(art)
                count += 1
                overrides[name] = _serialize(raw, root)
    if overrides:
        tmp = path.with_name(path.name + ".fit.tmp")
        _rewrite_zip_preserving(path, tmp, overrides)
        tmp.replace(path)
    return count


def add_running_title_slot(path: str | Path, spec: WingSpec | None = None) -> list[str]:
    """Add the ``{{running_title}}`` box to even master pages with a cleaned title picture.

    The text takes the HEADING_1 style's face and colour at the odd-page wing's size,
    like :func:`~.chapter_header.apply_chapter_headers`, so both margins match.
    Master pages that already hold the slot are skipped (re-running is a no-op); an
    older vertically written slot box is replaced.
    Returns the master-page parts changed.
    """
    from .styles import role_map

    spec = spec or WingSpec()
    path = Path(path)
    clean = {h for h, n in _clean_hashes().items() if n in _EVEN_TITLE}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        raw = {n: z.read(n) for n in names if n.endswith(".xml")}
        hpf = etree.fromstring(z.read("Contents/content.hpf"))
        href = {
            item.get("id"): item.get("href")
            for item in hpf.iter("{http://www.idpf.org/2007/opf/}item")
        }
        bin_hash = {
            iid: hashlib.sha256(z.read(h)).hexdigest()
            for iid, h in href.items()
            if h and h.startswith("BinData/") and h in names
        }
    targets = []
    for name in sorted(n for n in raw if n.startswith("Contents/masterpage")):
        root = etree.fromstring(raw[name])
        if root.get("type") != "EVEN" or TITLE_ART_COMMENT.encode() in raw[name]:
            continue
        # an older slot: a vertically written text box holding the token — replace it
        for old in list(root.iter(_hp("rect"))):
            if any(t.text and SLOT in t.text for t in old.iter(_hp("t"))):
                old.getparent().remove(old)
        for pic in root.iter(_hp("pic")):
            img = pic.find(f"{{{_HC}}}img")
            if img is not None and bin_hash.get(img.get("binaryItemIDRef")) in clean:
                targets.append((name, root, pic))
                break
    if not targets:
        return []

    header = etree.fromstring(raw["Contents/header.xml"])
    h1 = role_map(path).get("HEADING_1")
    style = header.find(f".//{_hh('style')}[@id='{h1}']") if h1 is not None else None
    if style is None:
        return []
    look = _char_look(header, style.get("charPrIDRef"))
    next_id = 1 + max(
        (
            int(v)
            for xml in raw.values()
            for v in etree.fromstring(xml).xpath("//@id | //@instid")
            if v.isdigit() and int(v) < 2**31 - 16
        ),
        default=0,
    )
    overrides: dict[str, bytes] = {}
    for name, root, pic in targets:
        box = etree.fromstring(_title_box(next_id, look, spec))
        next_id += 2
        pic.addnext(box)
        overrides[name] = _serialize(raw[name], root)
    tmp = path.with_name(path.name + ".slot.tmp")
    _rewrite_zip_preserving(path, tmp, overrides)
    tmp.replace(path)
    return [n for n, _, _ in targets]
