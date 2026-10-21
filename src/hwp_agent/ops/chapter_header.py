"""Per-chapter side wing (쪽날개) for templates whose wing is baked into a picture.

JI's 과제 report forms (정책·전략·기반·센터과제) draw the odd-page side wing — the
chapter title running down the right margin ("Ⅰ. 세계경제 전망") — as **pixels in
the master page's full-page background JPG**, not as text or a field (issue #34).
A master page belongs to a whole section and every body chapter lands in one
section, so after ``build`` every odd page of every chapter shows the template's
sample chapter title.

:func:`apply_chapter_headers` fixes that without splitting sections (XML-built
content sections are rejected by Hangul, see ``docs/section-split.md``): each
HEADING_1 paragraph of such a section gets an **odd-page header control** holding a
white, borderless text box that covers the baked-in title and shows the chapter's
own title in vertical writing. A header control takes effect from the page it sits
on until the next one, so each chapter's odd pages carry that chapter's title; an
empty odd-page header at the start of the following section stops the last
chapter's wing from running on into it (header controls outlive their section).
The text uses the chapter heading's own charPr (font, colour, weight) at the wing's
size.

Sections without HEADING_1 paragraphs (표지·목차·요약…) and sections whose odd
master page has no full-page background picture are left untouched.

Measured with hwp2pdf (Hangul) on 2026-10-21 — why it looks the way it does:

* Hangul places an object held in a header control relative to the header origin,
  ignoring ``horzRelTo``/``vertRelTo`` (PAPER, PAGE and PARA all land the same):
  x at the left margin, y about :data:`_HEADER_ORIGIN_DY` below the top margin
  (3004-3025 for top margins 6503/8503/10503 and header margins 0/2000).
* A rotated text box (``rotationInfo``) turns the box but not its text, whatever
  the ``renderingInfo`` matrices say — so the wing is written vertically (Hangul
  upright, top to bottom) instead of the picture's sideways glyphs.
"""

from __future__ import annotations

import copy
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from .container import _rewrite_zip_preserving

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_HC = "http://www.hancom.co.kr/hwpml/2011/core"
_HEADER_ORIGIN_DY = 3010
_IDENTITY = '<hc:{0} e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


def _hh(tag: str) -> str:
    return f"{{{_HH}}}{tag}"


@dataclass
class WingSpec:
    """Where the baked-in wing title sits, in HWPUNIT on the paper.

    Defaults are measured on the 정책과제 form's body background (image8, 2244×3071
    px over 53858×73700 HWPUNIT, 24 HWPUNIT/px): the title glyphs span x 2068-2094
    px and y 355-542 px, in navy bold. The box is a little larger than the glyphs
    so it hides them fully.
    """

    #: distance from the paper's right edge to the wing's centre line
    right_to_center: int = 3900
    #: top of the box; the title starts just below it
    top: int = 8300
    #: width of the box (across the text)
    thickness: int = 1100
    #: height of the box (along the text); long enough for ~40 characters
    length: int = 30000
    #: glyph height of the wing title (HWPUNIT, 100 = 1pt)
    char_height: int = 800
    fill: str = "#FFFFFF"


@dataclass
class ChapterHeaderResult:
    sections: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    #: sections with chapter headings but no picture wing to cover
    skipped: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.titles)


def _para_text(p: etree._Element) -> str:
    return "".join(
        t.text or "" for run in p.findall(_hp("run")) for t in run.findall(_hp("t"))
    ).strip()


def _serialize(raw: bytes, root) -> bytes:
    """*root* as bytes, keeping the part's original XML declaration verbatim."""
    head = raw[: raw.index(b"<", raw.index(b"?>"))] if raw.startswith(b"<?xml") else b""
    return head + etree.tostring(root, encoding="UTF-8")


def _section_parts(names) -> list[str]:
    parts = [n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)]
    return sorted(parts, key=lambda n: int(re.search(r"(\d+)", n).group(1)))


def _odd_master_has_background(zf: zipfile.ZipFile, sec_pr, names: set[str]) -> bool:
    """True if the section's odd (or both-sides) master page is a full-page picture."""
    page_pr = sec_pr.find(_hp("pagePr"))
    paper_w = int(page_pr.get("width", "0")) if page_pr is not None else 0
    for ref in sec_pr.findall(_hp("masterPage")):
        part = f"Contents/{ref.get('idRef')}.xml"
        if part not in names:
            continue
        mp = etree.fromstring(zf.read(part))
        if mp.get("type") not in ("ODD", "BOTH"):
            continue
        for pic in mp.iter(_hp("pic")):
            sz = pic.find(_hp("sz"))
            if sz is not None and paper_w and int(sz.get("width", "0")) >= paper_w * 0.9:
                return True
    return False


def _ensure_variant(props, tag: str, base_id: str, modify) -> str:
    """Id of a copy of ``<tag id=base_id>`` changed by *modify* (reused if present)."""
    base = props.find(f"{_hh(tag)}[@id='{base_id}']")
    if base is None:
        base = props.find(_hh(tag))
    want = copy.deepcopy(base)
    want.attrib.pop("id", None)
    modify(want)
    key = etree.tostring(want)
    existing = props.findall(_hh(tag))
    for el in existing:
        c = copy.deepcopy(el)
        c.attrib.pop("id", None)
        if etree.tostring(c) == key:
            return el.get("id")
    new_id = str(len(existing))
    attrs = dict(want.attrib)
    want.attrib.clear()
    want.attrib.update({"id": new_id, **attrs})  # id first, as Hangul writes it
    props.append(want)
    props.set("itemCnt", str(len(existing) + 1))
    return new_id


def _wing_char_pr(char_props, base_id: str, height: int) -> str:
    return _ensure_variant(char_props, "charPr", base_id, lambda el: el.set("height", str(height)))


def _wing_para_pr(para_props, base_id: str) -> str:
    """A LEFT-aligned (top, in vertical writing), margin-free copy of *base_id*."""

    def modify(el) -> None:
        el.find(_hh("align")).set("horizontal", "LEFT")
        for tag in ("intent", "left", "right", "prev", "next"):
            for m in el.iter(f"{{{_HC}}}{tag}"):
                m.set("value", "0")

    return _ensure_variant(para_props, "paraPr", base_id, modify)


def _header_ctrl(header_id: int, text_width: int, para_pr: str, char_pr: str, body: str) -> str:
    """An odd-page header control whose single paragraph's run holds *body*."""
    return (
        f'<hp:ctrl xmlns:hp="{_HP}" xmlns:hc="{_HC}">'
        f'<hp:header id="{header_id}" applyPageType="ODD">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="{text_width}" textHeight="0" '
        f'hasTextRef="0" hasNumRef="0">'
        f'<hp:p id="0" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" '
        f'merged="0"><hp:run charPrIDRef="{char_pr}">{body}<hp:t/></hp:run></hp:p>'
        f'</hp:subList></hp:header></hp:ctrl>'
    )


def _wing_box(
    *, title: str, shape_id: int, char_pr: str, para_pr: str, paper_w: int,
    margin_left: int, margin_top: int, spec: WingSpec,
) -> str:
    """The wing: a white, borderless, vertically-written text box (module docstring)."""
    w, h = spec.thickness, spec.length
    # header-hosted objects sit relative to the header origin, not the paper
    horz = paper_w - spec.right_to_center - w // 2 - margin_left
    vert = spec.top - margin_top - _HEADER_ORIGIN_DY
    mats = "".join(_IDENTITY.format(m) for m in ("transMatrix", "scaMatrix", "rotMatrix"))
    return (
        f'<hp:rect id="{shape_id}" zOrder="100" numberingType="PICTURE" '
        f'textWrap="IN_FRONT_OF_TEXT" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        f'href="" groupLevel="0" instid="{shape_id + 1}" ratio="0">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{w}" height="{h}"/>'
        f'<hp:curSz width="{w}" height="{h}"/><hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{w // 2}" centerY="{h // 2}" rotateimage="1"/>'
        f'<hp:renderingInfo>{mats}</hp:renderingInfo>'
        f'<hp:lineShape color="#000000" width="0" style="NONE" endCap="FLAT" '
        f'headStyle="NORMAL" tailStyle="NORMAL" headfill="1" tailfill="1" '
        f'headSz="SMALL_SMALL" tailSz="SMALL_SMALL" outlineStyle="NORMAL" alpha="0"/>'
        f'<hc:fillBrush><hc:winBrush faceColor="{spec.fill}" hatchColor="#000000" '
        f'alpha="0"/></hc:fillBrush>'
        f'<hp:shadow type="NONE" color="#B2B2B2" offsetX="0" offsetY="0" alpha="0"/>'
        f'<hp:drawText lastWidth="{w}" name="" editable="0">'
        f'<hp:subList id="" textDirection="VERTICAL" lineWrap="BREAK" vertAlign="CENTER" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
        f'hasTextRef="0" hasNumRef="0">'
        f'<hp:p id="0" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" '
        f'columnBreak="0" merged="0"><hp:run charPrIDRef="{char_pr}">'
        f'<hp:t>{escape(title)}</hp:t></hp:run></hp:p></hp:subList>'
        f'<hp:textMargin left="0" right="0" top="200" bottom="0"/></hp:drawText>'
        f'<hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/><hc:pt2 x="{w}" y="{h}"/>'
        f'<hc:pt3 x="0" y="{h}"/>'
        f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" '
        f'protect="0"/>'
        f'<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="0" allowOverlap="1" '
        f'holdAnchorAndSO="0" vertRelTo="PAPER" horzRelTo="PAPER" vertAlign="TOP" '
        f'horzAlign="LEFT" vertOffset="{vert & 0xFFFFFFFF}" '
        f'horzOffset="{horz & 0xFFFFFFFF}"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:shapeComment>사각형입니다.</hp:shapeComment></hp:rect>'
    )


def _prepend_run(p, ctrl_xml: str, char_pr: str) -> None:
    """Put a run holding *ctrl_xml* before the paragraph's text.

    It goes after any secPr run and after header controls already there, so a later
    header (a chapter's wing) wins over an earlier one (the blanking header).
    """
    run = etree.Element(_hp("run"), charPrIDRef=char_pr)
    run.append(etree.fromstring(ctrl_xml))
    runs = p.findall(_hp("run"))
    at = 0
    for i, r in enumerate(runs):
        if r.find(_hp("secPr")) is not None or r.find(f"{_hp('ctrl')}/{_hp('header')}") is not None:
            at = i + 1
    if at < len(runs):
        runs[at].addprevious(run)
    elif runs:
        runs[-1].addnext(run)
    else:
        p.insert(0, run)


def apply_chapter_headers(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    spec: WingSpec | None = None,
) -> ChapterHeaderResult:
    """Give each chapter its own odd-page side wing (see module docstring).

    Rewrites *dst* (default: *src* in place) changing only ``header.xml`` and the
    sections it touches; a document with nothing to do is copied unchanged.
    """
    from .styles import role_map

    src = Path(src)
    dst = Path(dst) if dst is not None else src
    spec = spec or WingSpec()
    result = ChapterHeaderResult()
    h1 = role_map(src).get("HEADING_1")

    overrides: dict[str, bytes] = {}
    if h1 is not None:
        with zipfile.ZipFile(src) as zf:
            names = set(zf.namelist())
            parts = _section_parts(names)
            raw = {n: zf.read(n) for n in parts + ["Contents/header.xml"]}
            header_root = etree.fromstring(raw["Contents/header.xml"])
            char_props = header_root.find(f".//{_hh('charProperties')}")
            para_props = header_root.find(f".//{_hh('paraProperties')}")
            sections = {n: etree.fromstring(raw[n]) for n in parts}
            next_id = 1 + max(
                (int(v) for root in sections.values() for el in root.iter()
                 for v in (el.get("id"), el.get("instid"))
                 if v and v.isdigit() and int(v) < 2**31 - 16),
                default=0,
            )
            touched: list[str] = []
            for i, part in enumerate(parts):
                root = sections[part]
                heads = [p for p in root.findall(_hp("p"))
                         if p.get("styleIDRef") == h1 and _para_text(p)]
                if not heads:
                    continue
                sec_pr = root.find(f".//{_hp('secPr')}")
                if sec_pr is None or not _odd_master_has_background(zf, sec_pr, names):
                    result.skipped.append(part)
                    continue
                page_pr = sec_pr.find(_hp("pagePr"))
                margin = page_pr.find(_hp("margin"))
                paper_w = int(page_pr.get("width"))
                left, top = int(margin.get("left")), int(margin.get("top"))
                text_width = paper_w - left - int(margin.get("right"))
                for p in heads:
                    runs = p.findall(_hp("run"))
                    base_char = runs[0].get("charPrIDRef", "0") if runs else "0"
                    char_pr = _wing_char_pr(char_props, base_char, spec.char_height)
                    para_pr = _wing_para_pr(para_props, p.get("paraPrIDRef", "0"))
                    box = _wing_box(
                        title=_para_text(p), shape_id=next_id + 1, char_pr=char_pr,
                        para_pr=para_pr, paper_w=paper_w, margin_left=left,
                        margin_top=top, spec=spec,
                    )
                    _prepend_run(p, _header_ctrl(next_id, text_width, para_pr, char_pr, box),
                                 base_char)
                    next_id += 3
                    result.titles.append(_para_text(p))
                result.sections.append(part)
                touched.append(part)
                # header controls outlive their section: blank the odd header at the
                # start of the next one, so the last chapter's wing doesn't run into it
                if i + 1 < len(parts):
                    nxt = parts[i + 1]
                    first = sections[nxt].find(_hp("p"))
                    if first is not None:
                        run0 = first.find(_hp("run"))
                        cp = run0.get("charPrIDRef", "0") if run0 is not None else "0"
                        pp = first.get("paraPrIDRef", "0")
                        _prepend_run(first, _header_ctrl(next_id, 0, pp, cp, ""), cp)
                        next_id += 3
                        touched.append(nxt)
            for part in dict.fromkeys(touched):
                overrides[part] = _serialize(raw[part], sections[part])
            if overrides:
                overrides["Contents/header.xml"] = _serialize(
                    raw["Contents/header.xml"], header_root
                )
    if not overrides:
        if src != dst:
            dst.write_bytes(src.read_bytes())
        return result
    tmp = dst.with_suffix(dst.suffix + ".wing.tmp")
    _rewrite_zip_preserving(src, tmp, overrides)
    tmp.replace(dst)
    return result
