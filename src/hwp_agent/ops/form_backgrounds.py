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
* :func:`add_running_title_slot` puts the report title back as text: a borderless,
  transparent, vertically written ``{{running_title}}`` box where the picture's
  title was, on each even master page showing a cleaned picture. ``report_meta``
  fills it (``running_title`` ← meta, else the title). The odd-page chapter title
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
    _HP,
    _IDENTITY,
    WingSpec,
    _hh,
    _hp,
    _serialize,
    _wing_char_pr,
    _wing_para_pr,
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


def _title_box(shape_id: int, char_pr: str, para_pr: str, spec: WingSpec) -> str:
    """Transparent vertical text box mirroring the odd-page wing on the left margin.

    Master-page objects are placed on the paper (unlike header-hosted ones), so the
    offsets are plain paper coordinates. Measured on the even pictures (2244×3071 px
    over 53858×73700 HWPUNIT): the title glyphs span x 148-176 px, from y 354 px.
    """
    w, h = spec.thickness, spec.length
    horz = spec.right_to_center - w // 2  # same distance from the left edge
    vert = spec.top
    mats = "".join(_IDENTITY.format(m) for m in ("transMatrix", "scaMatrix", "rotMatrix"))
    return (
        f'<hp:rect xmlns:hp="{_HP}" xmlns:hc="{_HC}" id="{shape_id}" zOrder="100" '
        f'numberingType="NONE" textWrap="IN_FRONT_OF_TEXT" textFlow="BOTH_SIDES" lock="0" '
        f'dropcapstyle="None" href="" groupLevel="0" instid="{shape_id + 1}" ratio="0">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{w}" height="{h}"/>'
        f'<hp:curSz width="{w}" height="{h}"/><hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{w // 2}" centerY="{h // 2}" rotateimage="1"/>'
        f'<hp:renderingInfo>{mats}</hp:renderingInfo>'
        f'<hp:lineShape color="#000000" width="0" style="NONE" endCap="FLAT" '
        f'headStyle="NORMAL" tailStyle="NORMAL" headfill="1" tailfill="1" '
        f'headSz="SMALL_SMALL" tailSz="SMALL_SMALL" outlineStyle="NORMAL" alpha="0"/>'
        f'<hp:shadow type="NONE" color="#B2B2B2" offsetX="0" offsetY="0" alpha="0"/>'
        f'<hp:drawText lastWidth="{w}" name="" editable="0">'
        f'<hp:subList id="" textDirection="VERTICAL" lineWrap="BREAK" vertAlign="CENTER" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
        f'hasTextRef="0" hasNumRef="0">'
        f'<hp:p id="0" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" '
        f'columnBreak="0" merged="0"><hp:run charPrIDRef="{char_pr}">'
        f"<hp:t>{SLOT}</hp:t></hp:run></hp:p></hp:subList>"
        f'<hp:textMargin left="0" right="0" top="200" bottom="0"/></hp:drawText>'
        f'<hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/><hc:pt2 x="{w}" y="{h}"/>'
        f'<hc:pt3 x="0" y="{h}"/>'
        f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" '
        f'protect="0"/>'
        f'<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="0" allowOverlap="1" '
        f'holdAnchorAndSO="0" vertRelTo="PAPER" horzRelTo="PAPER" vertAlign="TOP" '
        f'horzAlign="LEFT" vertOffset="{vert}" horzOffset="{horz}"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        f"<hp:shapeComment>사각형입니다.</hp:shapeComment></hp:rect>"
    )


def add_running_title_slot(path: str | Path, spec: WingSpec | None = None) -> list[str]:
    """Add the ``{{running_title}}`` box to even master pages with a cleaned title picture.

    The text takes the HEADING_1 style's character shape at the odd-page wing's
    size, like :func:`~.chapter_header.apply_chapter_headers`, so both margins match.
    Master pages that already hold the slot are skipped (re-running is a no-op).
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
        if root.get("type") != "EVEN" or SLOT.encode() in raw[name]:
            continue
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
    char_pr = _wing_char_pr(
        header.find(f".//{_hh('charProperties')}"), style.get("charPrIDRef"), spec.char_height
    )
    para_pr = _wing_para_pr(header.find(f".//{_hh('paraProperties')}"), style.get("paraPrIDRef"))
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
        box = etree.fromstring(_title_box(next_id, char_pr, para_pr, spec))
        next_id += 2
        pic.addnext(box)
        overrides[name] = _serialize(raw[name], root)
    overrides["Contents/header.xml"] = _serialize(raw["Contents/header.xml"], header)
    tmp = path.with_name(path.name + ".slot.tmp")
    _rewrite_zip_preserving(path, tmp, overrides)
    tmp.replace(path)
    return [n for n, _, _ in targets]
