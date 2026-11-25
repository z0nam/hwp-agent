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
white, borderless box that covers the baked-in title and the chapter's own title
as a turned 글맵시. A header control takes effect from the page it sits
on until the next one, so each chapter's odd pages carry that chapter's title. A
running header the template already has is cloned into each wing header, and the
header in force before the wings is put back at the start of the following section
so the last chapter's wing doesn't run on into it (header controls outlive their
section).
The title uses the chapter heading's own font, colour and weight at the wing's
size.

Sections without HEADING_1 paragraphs (표지·목차·요약…) and sections whose odd
master page has no full-page background picture are left untouched.

Measured with hwp2pdf (Hangul) on 2026-10-21 — why it looks the way it does:

* Hangul places an object held in a header control relative to the header origin,
  ignoring ``horzRelTo``/``vertRelTo`` (PAPER, PAGE and PARA all land the same):
  x at the left margin, y about :data:`_HEADER_ORIGIN_DY` below the top margin
  (3004-3025 for top margins 6503/8503/10503 and header margins 0/2000).
* A rotated text box (``rotationInfo``) turns the box but not its text, whatever
  the ``renderingInfo`` matrices say. A 글맵시 (``hp:textart``) turns its glyphs with
  it (2026-11-25), so the wing title is one, turned to read bottom to top like the
  picture's, over a white box hiding the picture's own title.
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
    #: em of the wing title, also the 글맵시's thickness (HWPUNIT, 100 = 1pt)
    char_height: int = 700
    #: gap from :attr:`top` to where the title's glyphs start
    text_inset: int = 200
    fill: str = "#FFFFFF"
    #: paper the geometry was measured on; other paper sizes are left alone
    paper_width: int = 53858
    paper_height: int = 73700


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


def _has_picture_wing(zf: zipfile.ZipFile, sec_pr, names: set[str], spec: WingSpec) -> bool:
    """True if the section looks like the JI 과제 forms the wing geometry was measured on.

    The odd (or both-sides) master page must hold a picture laid behind the text
    that covers the whole paper, and the paper must be the size *spec* was measured
    on — its coordinates mean nothing on other paper, and a page-wide banner or logo
    on some other template must not get a white box painted over it.
    """
    page_pr = sec_pr.find(_hp("pagePr"))
    if page_pr is None:
        return False
    paper_w, paper_h = int(page_pr.get("width", "0")), int(page_pr.get("height", "0"))
    if (abs(paper_w - spec.paper_width) > spec.paper_width * 0.01
            or abs(paper_h - spec.paper_height) > spec.paper_height * 0.01):
        return False
    for ref in sec_pr.findall(_hp("masterPage")):
        part = f"Contents/{ref.get('idRef')}.xml"
        if part not in names:
            continue
        mp = etree.fromstring(zf.read(part))
        if mp.get("type") not in ("ODD", "BOTH"):
            continue
        for pic in mp.iter(_hp("pic")):
            sz = pic.find(_hp("sz"))
            if (
                sz is not None
                and pic.get("textWrap") == "BEHIND_TEXT"
                and int(sz.get("width", "0")) >= paper_w * 0.95
                and int(sz.get("height", "0")) >= paper_h * 0.95
            ):
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
    # one past the highest id — not the element count, which collides with an existing id
    # whenever the ids are not 0..n-1 (other steps append variants as max id + 1)
    ids = [int(el.get("id")) for el in existing if (el.get("id") or "").isdigit()]
    new_id = str(max(ids, default=-1) + 1)
    attrs = dict(want.attrib)
    want.attrib.clear()
    want.attrib.update({"id": new_id, **attrs})  # id first, as Hangul writes it
    props.append(want)
    props.set("itemCnt", str(len(existing) + 1))
    return new_id


def _wing_char_pr(char_props, base_id: str, height: int) -> str:
    return _ensure_variant(char_props, "charPr", base_id, lambda el: el.set("height", str(height)))


def _wing_para_pr(para_props, base_id: str) -> str:
    """A LEFT-aligned (top, in vertical writing), margin-free, **non-outline** copy of
    *base_id*.

    The wing paragraphs copy the chapter title's paragraph shape, which is an OUTLINE
    heading; kept as is, Hangul's 제목 차례 collects the wing text too and every chapter
    title appears twice (#113). The wing is decoration, so its heading becomes NONE.
    """

    def modify(el) -> None:
        el.find(_hh("align")).set("horizontal", "LEFT")
        heading = el.find(_hh("heading"))
        if heading is not None:
            heading.set("type", "NONE")
            heading.set("idRef", "0")
            heading.set("level", "0")
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
    *, title: str, shape_id: int, paper_w: int, margin_left: int, margin_top: int,
    spec: WingSpec, look: tuple[str, str, bool],
) -> str:
    """The wing: a white, borderless box over the picture's title, and the chapter's
    own title as a 글맵시 turned to read bottom to top like the picture's (module
    docstring). Takes ids *shape_id* … *shape_id* + 3.
    """
    w, h = spec.thickness, spec.length
    # header-hosted objects sit relative to the header origin, not the paper
    dy = margin_top + _HEADER_ORIGIN_DY
    horz = paper_w - spec.right_to_center - w // 2 - margin_left
    vert = spec.top - dy
    mats = "".join(_IDENTITY.format(m) for m in ("transMatrix", "scaMatrix", "rotMatrix"))
    face, color, bold = look
    art = _title_art(
        text=title, shape_id=shape_id + 2, angle=270, em=spec.char_height,
        horz=paper_w - spec.right_to_center - spec.char_height // 2 - margin_left,
        # a header-hosted 글맵시 sits from the top margin, without the box's extra
        # header-origin drop (hwp2pdf, 2026-11-25)
        vert=spec.top + spec.text_inset - margin_top, face=face, color=color, bold=bold,
    ).replace(f' xmlns:hp="{_HP}" xmlns:hc="{_HC}"', "", 1)
    return (
        f'<hp:rect id="{shape_id}" zOrder="100" numberingType="NONE" '
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
        + art
    )


#: advance widths (per 1000 em) of KoPubWorld돋움체 Bold, the forms' heading face, for
#: ASCII 32-126; used to size a title's 글맵시 box to its text
_ASCII_ADV = [int(v) for v in (
    "290 300 320 590 590 874 706 180 310 310 446 590 300 570 300 446 563 563 563 563 "
    "563 563 563 563 563 563 316 316 425 590 425 486 882 662 664 664 713 609 555 712 "
    "718 283 500 609 555 872 718 758 609 758 664 601 555 718 621 990 609 609 555 310 "
    "446 310 434 490 291 506 562 506 562 562 341 562 562 232 232 506 232 891 562 549 "
    "562 562 341 506 341 562 504 802 506 504 451 310 386 310 527"
).split()]
#: the square a 글맵시 lays its text out in (orgSz); scaMatrix stretches it to curSz
_TEXTART_ORG = 14173
#: the pictures set their titles tighter than the face's advances: "Ⅰ. 세계경제 전망"
#: runs 188 px where the advances at its glyph size give 204 (hwp2pdf, 2026-11-25)
_TITLE_TRACKING = 0.92
#: outline (HWPUNIT) in the fill colour, bringing the 글맵시's weight up to the
#: pictures' (ink per area at 300 dpi: picture 0.38, no outline 0.34)
_TITLE_STROKE = 10
#: shapeComment marking the title 글맵시 this module writes (see :func:`fit_title_art`)
TITLE_ART_COMMENT = "쪽 제목 글맵시입니다."


def _advance(text: str) -> int:
    """Width of *text* in 1/1000 em of the heading face (KoPubWorld돋움체 Bold)."""
    import unicodedata

    total = 0
    for ch in text:
        o = ord(ch)
        if 32 <= o < 127:
            total += _ASCII_ADV[o - 32]
        elif 0xAC00 <= o <= 0xD7A3 or 0x3130 <= o <= 0x318F:
            total += 872  # Hangul syllables and jamo
        elif ch in "·“”‘’":
            total += 319 if ch == "·" else 410 if ch in "“”" else 290
        elif unicodedata.east_asian_width(ch) in "WFA":
            total += 900  # Ⅰ-Ⅹ, ·, …, full-width forms
        else:
            total += 600
    return total


def _title_length(text: str, em: int) -> int:
    """Length of a title 글맵시 holding *text* at *em* (HWPUNIT)."""
    return max(1, int(_advance(text) * em * _TITLE_TRACKING) // 1000)


def _char_look(header_root, char_pr_id: str) -> tuple[str, str, bool]:
    """(Hangul font face, text colour, bold) of ``charPr`` *char_pr_id*."""
    cp = header_root.find(f".//{_hh('charPr')}[@id='{char_pr_id}']")
    if cp is None:
        return "KoPubWorld돋움체 Bold", "#06004B", True
    ref = cp.find(_hh("fontRef"))
    face = None
    if ref is not None:
        for ff in header_root.iter(_hh("fontface")):
            if ff.get("lang") == "HANGUL":
                font = ff.find(f"{_hh('font')}[@id='{ref.get('hangul', '0')}']")
                face = font.get("face") if font is not None else None
    return (face or "KoPubWorld돋움체 Bold", cp.get("textColor", "#000000"),
            cp.find(_hh("bold")) is not None)


def _title_art(
    *, text: str, shape_id: int, horz: int, vert: int, em: int, angle: int,
    face: str, color: str, bold: bool,
) -> str:
    """A 글맵시 (text art) showing *text* turned *angle* (90: reads top to bottom, 270:
    bottom to top) with its box's top-left at (*horz*, *vert*).

    Hangul turns a 글맵시's glyphs with the object, unlike a text box's text (module
    docstring). The geometry follows what Hangul itself writes (checked on a Hangul
    re-save, 2026-11-25): text laid out in an ``orgSz`` square stretched to ``curSz``
    by ``scaMatrix``; ``sz`` and ``rotationInfo``'s centre are the box *after* turning,
    and ``rotMatrix`` turns the stretched box back onto it. The glyphs fill the box,
    so it is sized to *text* at *em* from the heading face's advance widths.
    """
    w = _title_length(text, em)  # along the text
    h = em  # across it
    rot = (0, -1, h, 1, 0, 0) if angle == 90 else (0, 1, 0, -1, 0, w)
    org = _TEXTART_ORG
    # like the heading itself: a bold charPr on the Bold face (the pictures' weight)
    style = "진하게" if bold else "보통"
    attr = escape(text, {'"': "&quot;"})
    return (
        f'<hp:textart xmlns:hp="{_HP}" xmlns:hc="{_HC}" id="{shape_id}" zOrder="101" '
        f'numberingType="NONE" textWrap="IN_FRONT_OF_TEXT" textFlow="BOTH_SIDES" lock="0" '
        f'dropcapstyle="None" href="" groupLevel="0" instid="{shape_id + 1}" text="{attr}">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{org}" height="{org}"/>'
        f'<hp:curSz width="{w}" height="{h}"/><hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="{angle}" centerX="{h // 2}" centerY="{w // 2}" '
        f'rotateimage="1"/><hp:renderingInfo>{_IDENTITY.format("transMatrix")}'
        f'<hc:scaMatrix e1="{w / org:.6f}" e2="0" e3="0" e4="0" e5="{h / org:.6f}" e6="0"/>'
        f'<hc:rotMatrix e1="{rot[0]}" e2="{rot[1]}" e3="{rot[2]}" e4="{rot[3]}" '
        f'e5="{rot[4]}" e6="{rot[5]}"/></hp:renderingInfo>'
        f'<hp:lineShape color="{color}" width="{_TITLE_STROKE}" style="SOLID" '
        f'endCap="ROUND" headStyle="NORMAL" tailStyle="NORMAL" headfill="0" tailfill="0" '
        f'headSz="SMALL_SMALL" tailSz="SMALL_SMALL" outlineStyle="INNER" alpha="0"/>'
        f'<hc:fillBrush><hc:winBrush faceColor="{color}" hatchColor="#000000" alpha="0"/>'
        f'</hc:fillBrush>'
        f'<hp:shadow type="NONE" color="#B2B2B2" offsetX="0" offsetY="0" alpha="0"/>'
        f'<hc:pt0 x="0" y="0"/><hc:pt1 x="{org}" y="0"/><hc:pt2 x="{org}" y="{org}"/>'
        f'<hc:pt3 x="0" y="{org}"/>'
        f'<hp:textartPr fontName="{escape(face, {chr(34): "&quot;"})}" fontStyle="{style}" '
        f'fontType="TTF" textShape="RECTANGLE" lineSpacing="120" charSpacing="100" '
        f'align="LEFT"><hp:shadow type="NONE" color="#000000" offsetX="0" offsetY="0" '
        f'alpha="0"/></hp:textartPr>'
        f'<hp:sz width="{h}" widthRelTo="ABSOLUTE" height="{w}" heightRelTo="ABSOLUTE" '
        f'protect="0"/>'
        f'<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="0" allowOverlap="1" '
        f'holdAnchorAndSO="0" vertRelTo="PAPER" horzRelTo="PAPER" vertAlign="TOP" '
        f'horzAlign="LEFT" vertOffset="{vert & 0xFFFFFFFF}" '
        f'horzOffset="{horz & 0xFFFFFFFF}"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        f"<hp:shapeComment>{TITLE_ART_COMMENT}</hp:shapeComment></hp:textart>"
    )


def fit_title_art(art, text: str) -> None:
    """Re-size a title 글맵시 (lxml element) to *text*, keeping its top, centre line,
    thickness and turn — for a slot filled after the box was made."""
    h = int(art.find(_hp("curSz")).get("height"))
    w = _title_length(text, h)
    art.set("text", text)
    art.find(_hp("curSz")).set("width", str(w))
    art.find(_hp("sz")).set("height", str(w))
    art.find(_hp("rotationInfo")).set("centerY", str(w // 2))
    ri = art.find(_hp("renderingInfo"))
    ri.find(f"{{{_HC}}}scaMatrix").set("e1", f"{w / _TEXTART_ORG:.6f}")
    if art.find(_hp("rotationInfo")).get("angle") == "270":
        ri.find(f"{{{_HC}}}rotMatrix").set("e6", str(w))


def _prepend_run(p, ctrl, char_pr: str, *, after_headers: bool = True) -> None:
    """Put a run holding the *ctrl* element before the paragraph's text.

    It goes after any secPr run and — with *after_headers* — after header controls
    already there, so a chapter's wing wins over the paragraph's own headers. The
    restoring header at a section start goes before them instead, so the section's
    own headers still win over it.
    """
    run = etree.Element(_hp("run"), charPrIDRef=char_pr)
    run.append(ctrl)
    runs = p.findall(_hp("run"))
    at = 0
    for i, r in enumerate(runs):
        if r.find(_hp("secPr")) is not None or (
            after_headers and r.find(f"{_hp('ctrl')}/{_hp('header')}") is not None
        ):
            at = i + 1
    if at < len(runs):
        runs[at].addprevious(run)
    elif runs:
        runs[-1].addnext(run)
    else:
        p.insert(0, run)


def _odd_header_ctrl(active, header_id: int, renumber, text_width: int, para_pr: str,
                     char_pr: str, box: str):
    """An odd-page header showing what *active* shows, plus *box* (may be empty).

    *active* is the odd/both-sides header in force at that point (or None). Its
    content is cloned — object ids renumbered — so a template's own running header
    survives on the chapter's odd pages; the wing box rides in its first paragraph
    as a floating object, adding no line.
    """
    if active is None:
        return etree.fromstring(_header_ctrl(header_id, text_width, para_pr, char_pr, box))
    ctrl = etree.Element(_hp("ctrl"))
    header = etree.SubElement(ctrl, _hp("header"), id=str(header_id), applyPageType="ODD")
    sub = copy.deepcopy(active.find(_hp("subList")))
    renumber(sub)
    header.append(sub)
    if box:
        first = sub.find(_hp("p"))
        run = etree.Element(_hp("run"), charPrIDRef=char_pr)
        for obj in etree.fromstring(f'<x xmlns:hp="{_HP}" xmlns:hc="{_HC}">{box}</x>'):
            run.append(obj)  # the cover box and the title 글맵시
        lsa = first.find(_hp("linesegarray"))
        if lsa is not None:
            lsa.addprevious(run)
        else:
            first.append(run)
    return ctrl


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
            ids = iter(range(next_id, 2**31))

            def renumber(el) -> None:
                for e in el.iter():
                    if e.tag in (_hp("p"), _hp("subList")):
                        continue
                    for attr in ("id", "instid"):
                        v = e.get(attr)
                        if v and v.isdigit() and int(v) > 0:
                            e.set(attr, str(next(ids)))

            def odd_or_both(el) -> bool:
                return el.get("applyPageType") in ("ODD", "BOTH")

            # the odd/both-sides header in force so far (the document's own, never ours)
            active = None
            touched: list[str] = []
            restore_next = False
            for part in parts:
                root = sections[part]
                own = [h for h in root.iter(_hp("header")) if odd_or_both(h)]
                if restore_next:
                    # header controls outlive their section: at the start of the next
                    # one, put back what was in force before the wings, so the last
                    # chapter's wing doesn't run into it
                    restore_next = False
                    first = root.find(_hp("p"))
                    if first is not None:
                        run0 = first.find(_hp("run"))
                        cp = run0.get("charPrIDRef", "0") if run0 is not None else "0"
                        pp = first.get("paraPrIDRef", "0")
                        ctrl = _odd_header_ctrl(active, next(ids), renumber, 0, pp, cp, "")
                        _prepend_run(first, ctrl, cp, after_headers=False)
                        touched.append(part)
                heads = [p for p in root.findall(_hp("p"))
                         if p.get("styleIDRef") == h1 and _para_text(p)]
                sec_pr = root.find(f".//{_hp('secPr')}")
                target = bool(heads) and sec_pr is not None and _has_picture_wing(
                    zf, sec_pr, names, spec
                )
                if heads and not target:
                    result.skipped.append(part)
                if not target:
                    if own:
                        active = own[-1]
                    continue
                page_pr = sec_pr.find(_hp("pagePr"))
                margin = page_pr.find(_hp("margin"))
                paper_w = int(page_pr.get("width"))
                left, top = int(margin.get("left")), int(margin.get("top"))
                text_width = paper_w - left - int(margin.get("right"))
                for p in root.findall(_hp("p")):
                    # headers up to and including this paragraph are in force here
                    for h in p.iter(_hp("header")):
                        if odd_or_both(h):
                            active = h
                    if p not in heads:
                        continue
                    runs = p.findall(_hp("run"))
                    base_char = runs[0].get("charPrIDRef", "0") if runs else "0"
                    char_pr = _wing_char_pr(char_props, base_char, spec.char_height)
                    para_pr = _wing_para_pr(para_props, p.get("paraPrIDRef", "0"))
                    shape_id = next(ids)
                    for _ in range(3):  # cover's instid, 글맵시's id and instid
                        next(ids)
                    box = _wing_box(
                        title=_para_text(p), shape_id=shape_id, paper_w=paper_w,
                        margin_left=left, margin_top=top, spec=spec,
                        look=_char_look(header_root, base_char),
                    )
                    ctrl = _odd_header_ctrl(
                        active, next(ids), renumber, text_width, para_pr, char_pr, box
                    )
                    _prepend_run(p, ctrl, base_char)
                    result.titles.append(_para_text(p))
                result.sections.append(part)
                touched.append(part)
                restore_next = True
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



@dataclass
class FinalizeResult:
    #: the side-wing result, ``None`` when *chapter_header* was off
    wings: ChapterHeaderResult | None = None
    warnings: list[str] = field(default_factory=list)


def finalize_report(path: str | Path, *, chapter_header: bool = True) -> FinalizeResult:
    """The finishing steps ``write``/``build`` run on a filled report, in place (#95).

    Per-chapter odd-page side wings (:func:`apply_chapter_headers`, unless
    *chapter_header* is false), then uncaptioned decorations kept out of the 그림·표
    차례 (:func:`~.numbering.neutralize_uncaptioned_numbering`, #101 — a failure there
    is a warning, never an error). For callers of
    :func:`~.author.fill_from_markdown` / :func:`~.author.build_report` outside the
    CLI, which runs exactly this. Run it once per output: wings are added, not
    replaced.
    """
    from .numbering import neutralize_uncaptioned_numbering

    res = FinalizeResult()
    if chapter_header:
        res.wings = apply_chapter_headers(path)
    try:
        neutralize_uncaptioned_numbering(path)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        res.warnings.append(f"could not neutralize decoration numbering: {exc}")
    return res
