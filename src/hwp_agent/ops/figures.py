"""Figure building blocks for ``write`` (issue #31).

A Markdown figure ``![alt](assets/x.png){#fig_X}`` becomes a **1-column, 2-row,
borderless table**: the top cell holds the picture, the bottom cell the 출처/주
lines written directly under the image line. The figure carries a ``[그림 Ⅳ-2] alt``
caption and is numbered in its own figure sequence, never as a table (see
:func:`hwp_agent.ops.author._resolve_cross_refs`). Caption and picture number sit on a
group of the top cell's pictures (:func:`group_pictures`, #119) so Hangul's 그림 차례
collects the figure; only a figure whose image is missing keeps them on the table.

This module holds the pieces that don't depend on the author pipeline: parsing the
image line, registering a new ``BinData`` part, the ``<hp:pic>`` markup, a
borderless border fill, a centred paragraph property, and recognising a figure
table again on ``extract``. Image probing reuses :mod:`.images` (format sniffing,
pixel size, the ×75 pixel→HWPUNIT scale), and the slot rule from there holds here
too: the BinData extension always matches the bytes, because Hangul keys off it.
"""

from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from .images import _PX_TO_HWPUNIT, parse_alt_text, read_image_size, sniff_format

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_HC = "http://www.hancom.co.kr/hwpml/2011/core"
_OPF = "http://www.idpf.org/2007/opf/"

#: a figure on a line of its own: ``![alt](path)`` with an optional ``"title"`` and
#: an optional ``{#fig_X}`` anchor after it. The alt text may hold one level of
#: brackets, so a caption link ``![[기관](url) 자료](x.png)`` still parses.
FIGURE_LINE_RE = re.compile(
    r"""^\s*!\[(?P<alt>(?:[^\[\]]|\[[^\[\]]*\])*)\]
        \(\s*(?P<path><[^>]+>|(?:[^()\s]|\([^()\s]*\))+)(?:\s+"[^"]*")?\s*\)
        \s*(?:\{\s*\#(?P<label>[^{}\s]+)\s*\})?\s*$""",
    re.VERBOSE,
)
#: one image token (no anchors) — several of them on a line form one multi-image figure
_IMG = r"""!\[(?:[^\[\]]|\[[^\[\]]*\])*\]
        \(\s*(?:<[^>]+>|(?:[^()\s]|\([^()\s]*\))+)(?:\s+"[^"]*")?\s*\)"""
IMG_TOKEN_RE = re.compile(_IMG, re.VERBOSE)
#: 2+ image tokens on one line, optional trailing ``{#fig_X}`` (#96)
MULTI_FIGURE_LINE_RE = re.compile(
    rf"^\s*(?P<imgs>(?:{_IMG}\s*){{2,}})(?:\{{\s*\#(?P<label>[^{{}}\s]+)\s*\}})?\s*$",
    re.VERBOSE,
)
#: a leading ``[그림 …]`` caption framing (stripped on extract, rebuilt on write)
FIGURE_FRAMING_RE = re.compile(r"^\s*\[\s*그림\s*[^\]]*\]\s*")
#: shapeComment line carrying the figure's ``{#id}`` so ``extract`` can restore it
_LABEL_LINE = "그림 참조 id:"
_LABEL_LINE_RE = re.compile(re.escape(_LABEL_LINE) + r"\s*(\S+)")

#: formats Hangul embeds as pictures; the BinData extension used for each. Only
#: those whose size :func:`.images.read_image_size` reads without Pillow (an
#: optional, undeclared extra) — so TIFF is left out.
_EXT = {"png": "png", "jpeg": "jpg", "gif": "gif", "bmp": "bmp"}
_MEDIA = {"png": "image/png", "jpg": "image/jpg", "gif": "image/gif", "bmp": "image/bmp"}


@dataclass
class FigureBlock:
    """A Markdown image line, built as a 1×2 borderless figure table."""

    alt: str  # caption title (rendered after the "[그림 …]" framing)
    path: str  # image path as written (relative to the Markdown's directory)
    label: str | None = None  # {#fig_X} anchor, if any
    note: str | None = None  # 출처/주 line(s) directly below the image line
    blank_before: int = 0
    display: str = ""  # "그림 Ⅳ-2" — set by cross-ref resolution
    # further images of the same figure (left → right after ``path``), #96
    more_paths: list[str] = field(default_factory=list)
    # caption title taken from a "[그림 …] 제목" line written directly above the
    # image line (#110); None → the alt text is the caption, as before
    caption: str | None = None

    @property
    def paths(self) -> list[str]:
        return [self.path, *self.more_paths]

    def absorb(self, other: "FigureBlock") -> None:
        """Merge *other*'s images into this figure (one number, several pictures)."""
        self.more_paths.extend(other.paths)
        if not self.alt:
            self.alt = other.alt
        if self.label is None:
            self.label = other.label


def _clean_path(path: str) -> str:
    return path[1:-1] if path.startswith("<") and path.endswith(">") else path


def parse_figure_line(line: str) -> FigureBlock | None:
    """A :class:`FigureBlock` if *line* is a standalone image line, else None.

    A line of several images (``![좌](a.png) ![우](b.png)``) is one figure: the first
    non-empty alt is the caption title, the images sit side by side (#96).
    """
    multi = MULTI_FIGURE_LINE_RE.match(line)
    if multi:
        singles = [FIGURE_LINE_RE.match(t.group(0)) for t in IMG_TOKEN_RE.finditer(multi.group("imgs"))]
        figs = [
            FigureBlock(alt=m.group("alt").strip(), path=_clean_path(m.group("path")))
            for m in singles if m
        ]
        if len(figs) >= 2:
            head = figs[0]
            for other in figs[1:]:
                head.absorb(other)
            head.label = multi.group("label")
            return head
    m = FIGURE_LINE_RE.match(line)
    if not m:
        return None
    path = m.group("path")
    if path.startswith("<") and path.endswith(">"):
        path = path[1:-1]
    return FigureBlock(alt=m.group("alt").strip(), path=path, label=m.group("label"))


#: a CommonMark backslash escape of ASCII punctuation (``\_`` → ``_``)
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")


def rebase_figure_paths(markdown: str, base: Path) -> str:
    """Make relative image paths in standalone figure lines absolute under *base*.

    ``build`` concatenates several Markdown files that each name images relative to
    their own directory; rebasing each file before joining keeps those paths valid.
    A path that doesn't exist under *base* is left as written (it is then read
    relative to the manifest's folder).
    """
    out = []
    for line in markdown.split("\n"):
        if MULTI_FIGURE_LINE_RE.match(line):
            def _fix(tok: re.Match) -> str:
                return rebase_figure_paths(tok.group(0), base)

            line = IMG_TOKEN_RE.sub(_fix, line)
            out.append(line)
            continue
        m = FIGURE_LINE_RE.match(line)
        if m:
            raw = m.group("path")
            p = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
            p = _ESCAPE_RE.sub(r"\1", p)  # the file name, as write reads it
            if not Path(p).is_absolute() and "://" not in p and (base / p).is_file():
                new = str((base / p).resolve())
                line = line[: m.start("path")] + f"<{new}>" + line[m.end("path"):]
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# image data
# --------------------------------------------------------------------------- #
@dataclass
class LoadedImage:
    data: bytes
    ext: str  # BinData extension (png / jpg / …)
    px_w: int
    px_h: int


def load_image(path: Path) -> LoadedImage:
    """Read *path* and probe it; raise ``ValueError`` with a reason when unusable."""
    if not path.is_file():
        raise ValueError(f"image not found: {path}")
    data = path.read_bytes()
    fmt = sniff_format(data)
    if fmt not in _EXT:
        raise ValueError(f"unsupported image format ({fmt or 'unknown'}): {path}")
    px_w, px_h = read_image_size(data)
    if px_w <= 0 or px_h <= 0:
        raise ValueError(f"image has no size: {path}")
    return LoadedImage(data, _EXT[fmt], px_w, px_h)


def add_bin_image(doc, data: bytes, ext: str) -> str:
    """Store *data* as a new ``BinData/imageN.<ext>`` part and return its id.

    The id follows the template's own ``imageN`` naming and comes after every
    existing number, so a new picture never reuses the slot of a template picture
    (which is dropped later if nothing references it). The manifest gets the item
    with ``isEmbeded="1"`` like Hangul writes it; a header ``<hh:binDataList>`` (not
    present in Hangul's own HWPX, but some writers emit one) is kept in step,
    ``itemCnt`` included.
    """
    pkg = doc._package
    manifest = pkg._manifest_element()
    used = set()
    for item in manifest.findall(f"{{{_OPF}}}item") if manifest is not None else []:
        used.add(item.get("id") or "")
        used.add(Path(item.get("href") or "").stem)
    used.update(Path(n).stem for n in pkg.part_names() if str(n).startswith("BinData/"))
    nums = [int(m.group(1)) for u in used if (m := re.fullmatch(r"image(\d+)", u))]
    n = max(nums, default=0) + 1
    item_id = f"image{n}"
    href = f"BinData/{item_id}.{ext}"
    pkg.write(href, data)
    if manifest is not None:
        item = manifest.makeelement(
            f"{{{_OPF}}}item",
            {"id": item_id, "href": href, "media-type": _MEDIA.get(ext, f"image/{ext}"),
             "isEmbeded": "1"},
        )
        manifest.append(item)
        pkg._persist_manifest()
    header = _header(doc)
    bin_list = header.element.find(f".//{{{_HH}}}binDataList") if header is not None else None
    if bin_list is not None:
        ids = [int(b.get("id")) for b in bin_list if (b.get("id") or "").isdigit()]
        bin_list.append(bin_list.makeelement(
            f"{{{_HH}}}binItem",
            {"id": str(max(ids, default=0) + 1), "Type": "Embedding",
             "BinData": f"{item_id}.{ext}", "Format": ext},
        ))
        bin_list.set("itemCnt", str(len(bin_list)))
        header.mark_dirty()
    return item_id


def fit_extent(px_w: int, px_h: int, max_w: int, max_h: int | None) -> tuple[int, int]:
    """Display size (HWPUNIT) filling *max_w*, aspect kept, capped at *max_h*."""
    w = max_w
    h = round(w * px_h / px_w)
    if max_h and h > max_h:
        h = max_h
        w = round(h * px_w / px_h)
    return w, h


def picture_xml(
    item_id: str, img: LoadedImage, width: int, height: int, *,
    char_pr: str | None, source_name: str, label: str | None,
) -> str:
    """A ``<hp:run>`` holding one inline (treat-as-char) picture.

    The element order and attributes follow a picture Hangul saved itself (the
    bundled template's figure), except ``numberingType="NONE"``: the group the
    pictures go into (or, for a placeholder figure, the table) carries the number. ``imgDim``/``imgClip``/``orgSz`` are the native size
    (pixels ×75), ``sz``/``curSz`` the fitted display box, and the scale matrix maps
    one onto the other. The alt text (shapeComment) follows Hangul's wording so
    :func:`.images.parse_alt_text` reads the source name back.
    """
    ow, oh = img.px_w * _PX_TO_HWPUNIT, img.px_h * _PX_TO_HWPUNIT
    sx, sy = width / ow, height / oh
    pid = random.randint(0x10000000, 0x7FFFFFFF)
    comment = (
        f"그림입니다.\n원본 그림의 이름: {source_name}\n"
        f"원본 그림의 크기: 가로 {img.px_w}pixel, 세로 {img.px_h}pixel"
    )
    if label:
        comment += f"\n{_LABEL_LINE} {label}"
    c_attr = f' charPrIDRef="{char_pr}"' if char_pr is not None else ""
    return (
        f'<hp:run xmlns:hp="{_HP}" xmlns:hc="{_HC}"{c_attr}>'
        f'<hp:pic id="{pid}" zOrder="0" numberingType="NONE" textWrap="TOP_AND_BOTTOM" '
        f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" '
        f'instid="{pid ^ 0x5A5A5A5}" reverse="0">'
        f'<hp:offset x="0" y="0"/>'
        f'<hp:orgSz width="{ow}" height="{oh}"/>'
        f'<hp:curSz width="{width}" height="{height}"/>'
        f'<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{width // 2}" centerY="{height // 2}" '
        f'rotateimage="1"/>'
        f"<hp:renderingInfo>"
        f'<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="{sx:.6f}" e2="0" e3="0" e4="0" e5="{sy:.6f}" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f"</hp:renderingInfo>"
        f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{ow}" y="0"/>'
        f'<hc:pt2 x="{ow}" y="{oh}"/><hc:pt3 x="0" y="{oh}"/></hp:imgRect>'
        f'<hp:imgClip left="0" right="{ow}" top="0" bottom="{oh}"/>'
        f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:imgDim dimwidth="{ow}" dimheight="{oh}"/>'
        f'<hc:img binaryItemIDRef="{item_id}" bright="0" contrast="0" effect="REAL_PIC" '
        f'alpha="0"/>'
        f"<hp:effects/>"
        f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="{height}" '
        f'heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" '
        f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        f"<hp:shapeComment>{_esc(comment)}</hp:shapeComment>"
        f"</hp:pic><hp:t/></hp:run>"
    )


#: alt text Hangul gives a group object; ``write`` appends each picture's own alt
#: text (source name, size, ``{#id}``) after it — Hangul drops a grouped picture's
#: own shapeComment, so the group keeps them for ``extract``
GROUP_COMMENT = "묶음 개체입니다."
_PIC_COMMENT_HEAD = "그림입니다."


def group_pictures(para, pics: list, width: int, caption, *, gap: int) -> object:
    """Wrap the inline pictures *pics* of *para* into one group object (#119).

    Hangul's 그림 차례 collects captioned objects numbered as pictures — pictures and
    groups, not tables. A figure's caption therefore goes on a group of its pictures
    (#96: one or several, side by side) rather than on the figure table. The group is
    as wide as the text (*width*), with the pictures centred in it: an invisible
    full-width line keeps Hangul from shrinking the group to its pictures, so the
    caption spans the text width exactly as the table caption did. Child markup
    follows what Hangul writes when it saves such a group (child ``id`` 0, ``hc:img``
    right after ``renderingInfo``, no own size/position/alt text).

    The group takes the place of the first picture's run; the other picture runs and
    the spacer runs between them go. Returns the group element.
    """
    from lxml import etree

    hp, hc = f"{{{_HP}}}", f"{{{_HC}}}"
    heights = [int(q.find(hp + "sz").get("height")) for q in pics]
    widths = [int(q.find(hp + "sz").get("width")) for q in pics]
    h = max(heights)
    gid = random.randint(0x10000000, 0x7FFFFFFF)
    matrices = (
        '<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    )
    group = etree.fromstring(
        f'<hp:container xmlns:hp="{_HP}" xmlns:hc="{_HC}" id="{gid}" zOrder="0" '
        f'numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
        f'dropcapstyle="None" href="" groupLevel="0" instid="{gid ^ 0x5A5A5A5}">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{width}" height="{h}"/>'
        f'<hp:curSz width="0" height="0"/><hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{width // 2}" centerY="{h // 2}" rotateimage="1"/>'
        f"<hp:renderingInfo>{matrices}</hp:renderingInfo>"
        f'<hp:line id="0" zOrder="0" numberingType="NONE" textWrap="TOP_AND_BOTTOM" '
        f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="1" '
        f'instid="{gid ^ 0x1234567}" isReverseHV="0"><hp:offset x="0" y="0"/>'
        f'<hp:orgSz width="{width}" height="0"/><hp:curSz width="0" height="0"/>'
        f'<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{width // 2}" centerY="0" rotateimage="1"/>'
        f"<hp:renderingInfo>{matrices}</hp:renderingInfo>"
        f'<hp:lineShape color="#000000" width="0" style="NONE" endCap="FLAT" '
        f'headStyle="NORMAL" tailStyle="NORMAL" headfill="1" tailfill="1" '
        f'headSz="SMALL_SMALL" tailSz="SMALL_SMALL" outlineStyle="NORMAL" alpha="0"/>'
        f'<hp:shadow type="NONE" color="#B2B2B2" offsetX="0" offsetY="0" alpha="0"/>'
        f'<hc:startPt x="0" y="0"/><hc:endPt x="{width}" y="0"/></hp:line>'
        f"</hp:container>"
    )
    first_run = pics[0].getparent()
    comments = []
    x = max(0, (width - (sum(widths) + gap * (len(pics) - 1))) // 2)
    for q, w, qh in zip(pics, widths, heights, strict=True):
        run = q.getparent()
        run.remove(q)
        if run is not first_run:
            run.getparent().remove(run)
        y = h - qh
        q.set("id", "0")
        q.set("groupLevel", "1")
        q.find(hp + "offset").set("x", str(x))
        q.find(hp + "offset").set("y", str(y))
        trans = q.find(f"{hp}renderingInfo/{hc}transMatrix")
        trans.set("e3", str(x))
        trans.set("e6", str(y))
        img = q.find(hc + "img")
        q.remove(img)
        q.find(hp + "renderingInfo").addnext(img)
        for tag in ("sz", "pos", "outMargin", "shapeComment"):
            el = q.find(hp + tag)
            if el is not None:
                if tag == "shapeComment" and el.text:
                    comments.append(el.text)
                q.remove(el)
        group.append(q)
        x += w + gap
    # the spacer runs (a lone space) that sat between the pictures
    for run in list(para.findall(hp + "run")):
        if run is first_run or run.find(hp + "pic") is not None or len(run) != 1:
            continue
        t = run[0]
        if t.tag == hp + "t" and t.text == " " and len(t) == 0:
            para.remove(run)
    for xml in (
        f'<hp:sz xmlns:hp="{_HP}" width="{width}" widthRelTo="ABSOLUTE" height="{h}" '
        f'heightRelTo="ABSOLUTE" protect="0"/>',
        f'<hp:pos xmlns:hp="{_HP}" treatAsChar="1" affectLSpacing="0" flowWithText="1" '
        f'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
        f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>',
        f'<hp:outMargin xmlns:hp="{_HP}" left="0" right="0" top="0" bottom="0"/>',
    ):
        group.append(etree.fromstring(xml))
    sc = etree.SubElement(group, hp + "shapeComment")
    sc.text = "\n".join([GROUP_COMMENT, *comments])
    caption.set("width", str(width))
    caption.set("lastWidth", str(width))
    group.append(caption)
    first_run.insert(0, group)
    return group


def group_sources(group) -> list[tuple[str | None, str | None]]:
    """``(source_path, label)`` per picture of a ``write`` figure group, left to right."""
    comment = group.find(f"{{{_HP}}}shapeComment")
    text = (comment.text or "") if comment is not None else ""
    blocks = text.split(_PIC_COMMENT_HEAD)[1:]
    out = []
    for b in blocks:
        name, _, _ = parse_alt_text(b)
        m = _LABEL_LINE_RE.search(b)
        out.append((name, m.group(1) if m else None))
    return out


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# header properties
# --------------------------------------------------------------------------- #
def _header(doc):
    return doc._root._headers[0] if getattr(doc._root, "_headers", None) else None


def box_fill(doc) -> str | None:
    """Id of a plain box border fill — thin (0.12 mm) solid black on all four sides,
    no fill — created if the header has none (the 요약 상자 of the 정책이슈브리프, #91).

    Matched by *values*, not id: an issue brief's header and the form it came from
    number their border fills differently."""
    header = _header(doc)
    if header is None:
        return None
    fills = header.element.find(f".//{{{_HH}}}borderFills")
    if fills is None:
        return None

    def is_box(bf) -> bool:
        for s in ("left", "right", "top", "bottom"):
            side = bf.find(f"{{{_HH}}}{s}Border")
            if (
                side is None or side.get("type") != "SOLID"
                or side.get("width") != "0.12 mm"
                or (side.get("color") or "").upper() != "#000000"
            ):
                return False
        for diag in ("slash", "backSlash"):
            d = bf.find(f"{{{_HH}}}{diag}")
            if d is not None and d.get("type") not in (None, "NONE"):
                return False
        return bf.find(f".//{{{_HC}}}fillBrush") is None

    all_fills = fills.findall(f"{{{_HH}}}borderFill")
    for bf in all_fills:
        if is_box(bf):
            return bf.get("id")
    if not all_fills:
        return None
    new = copy.deepcopy(all_fills[0])
    ids = [int(b.get("id")) for b in all_fills if (b.get("id") or "").isdigit()]
    new_id = str(max(ids, default=0) + 1)
    new.set("id", new_id)
    for s in ("left", "right", "top", "bottom"):
        side = new.find(f"{{{_HH}}}{s}Border")
        if side is not None:
            side.set("type", "SOLID")
            side.set("width", "0.12 mm")
            side.set("color", "#000000")
    for diag in ("slash", "backSlash"):
        d = new.find(f"{{{_HH}}}{diag}")
        if d is not None:
            d.set("type", "NONE")
    for brush in new.findall(f".//{{{_HC}}}fillBrush"):
        brush.getparent().remove(brush)
    fills.append(new)
    fills.set("itemCnt", str(len(fills.findall(f"{{{_HH}}}borderFill"))))
    header.mark_dirty()
    return new_id


def borderless_fill(doc) -> str | None:
    """Id of a border fill with no lines and no fill, created if the header has none."""
    header = _header(doc)
    if header is None:
        return None
    fills = header.element.find(f".//{{{_HH}}}borderFills")
    if fills is None:
        return None

    def bare(bf) -> bool:
        sides = [bf.find(f"{{{_HH}}}{s}Border") for s in ("left", "right", "top", "bottom")]
        if any(s is None or s.get("type") != "NONE" for s in sides):
            return False
        for diag in ("slash", "backSlash"):
            d = bf.find(f"{{{_HH}}}{diag}")
            if d is not None and d.get("type") not in (None, "NONE"):
                return False
        return bf.find(f".//{{{_HC}}}fillBrush") is None

    all_fills = fills.findall(f"{{{_HH}}}borderFill")
    for bf in all_fills:
        if bare(bf):
            return bf.get("id")
    if not all_fills:
        return None
    new = copy.deepcopy(all_fills[0])
    ids = [int(b.get("id")) for b in all_fills if (b.get("id") or "").isdigit()]
    new.set("id", str(max(ids, default=0) + 1))
    for s in ("left", "right", "top", "bottom"):
        side = new.find(f"{{{_HH}}}{s}Border")
        if side is not None:
            side.set("type", "NONE")
    for diag in ("slash", "backSlash"):
        d = new.find(f"{{{_HH}}}{diag}")
        if d is not None:
            d.set("type", "NONE")
    for brush in new.findall(f".//{{{_HC}}}fillBrush"):
        brush.getparent().remove(brush)
    fills.append(new)
    fills.set("itemCnt", str(len(fills.findall(f"{{{_HH}}}borderFill"))))
    header.mark_dirty()
    return new.get("id")


def centered_para_pr(doc, base_para_pr_id, cache: dict[str, str]) -> str | None:
    """A CENTER-aligned, margin-free variant of *base_para_pr_id* (for the image cell).

    Body paragraphs often carry a left margin or first-line indent (the house form's
    body is indented 5600), which would push a full-width picture off its cell. The
    variant zeroes indent/left/right/prev/next and centres the line.
    """
    if base_para_pr_id is None:
        return None
    key = str(base_para_pr_id)
    if key in cache:
        return cache[key]
    header = _header(doc)
    pps = header._para_properties_element(create=False) if header is not None else None
    base = pps.find(f"{{{_HH}}}paraPr[@id='{key}']") if pps is not None else None
    if base is None:
        cache[key] = key
        return key
    new = copy.deepcopy(base)
    ids = [int(p.get("id")) for p in pps.findall(f"{{{_HH}}}paraPr")
           if (p.get("id") or "").isdigit()]
    new.set("id", str(max(ids, default=-1) + 1))
    align = new.find(f"{{{_HH}}}align")
    if align is None:
        align = new.makeelement(f"{{{_HH}}}align", {"vertical": "BASELINE"})
        new.insert(0, align)
    align.set("horizontal", "CENTER")
    heading = new.find(f"{{{_HH}}}heading")
    if heading is not None:  # never an outline/bullet paragraph
        heading.set("type", "NONE")
    for margin in new.iter(f"{{{_HH}}}margin"):
        for v in margin:
            v.set("value", "0")
    pps.append(new)
    pps.set("itemCnt", str(len(pps.findall(f"{{{_HH}}}paraPr"))))
    header.mark_dirty()
    cache[key] = new.get("id")
    return cache[key]


def body_height(section) -> int | None:
    """Text-area height = page height − top/bottom/header/footer margins (HWPUNIT)."""
    page = section.element.find(f".//{{{_HP}}}pagePr")
    if page is None or not page.get("height"):
        return None
    m = page.find(f"{{{_HP}}}margin")
    keys = ("top", "bottom", "header", "footer")
    used = sum(int(m.get(k, "0")) for k in keys) if m is not None else 0
    h = int(page.get("height")) - used
    return h if h > 0 else None


# --------------------------------------------------------------------------- #
# extract side
# --------------------------------------------------------------------------- #
def is_figure_table(tbl) -> bool:
    """A figure table ``write`` built — with its picture or, when the image was
    missing, its placeholder (numbered as a picture, ``[그림 …]`` caption)."""
    if figure_parts(tbl) is not None:
        return True
    cap = tbl.find(f"{{{_HP}}}caption")
    return (
        tbl.get("numberingType") == "PICTURE"
        and cap is not None
        and FIGURE_FRAMING_RE.match(_caption_text(cap)) is not None
    )


def _caption_text(cap) -> str:
    return "".join(t.text or "" for t in cap.iter(f"{{{_HP}}}t")).strip()


def figure_group(tbl):
    """The captioned picture group in *tbl*'s first cell (#119), else None."""
    first = tbl.find(f"{{{_HP}}}tr/{{{_HP}}}tc")
    if first is None:
        return None
    for group in first.iter(f"{{{_HP}}}container"):
        cap = group.find(f"{{{_HP}}}caption")
        if (group.get("numberingType") == "PICTURE" and cap is not None
                and FIGURE_FRAMING_RE.match(_caption_text(cap))):
            return group
    return None


def figure_parts(tbl) -> tuple[object, str] | None:
    """``(caption_holder, caption_text)`` when *tbl* is a figure table ``write``
    built, else None.

    A figure table is one column, one or two rows, a picture in the first cell, and
    a caption framed ``[그림 …]`` — on the picture group in that cell (#119) or, as
    built before and as JRI reports lay out figures by hand, on the table itself
    (those come back with the picture's original file name as the path). The holder
    is the element carrying the caption: the group, or the table.
    """
    if (tbl.get("colCnt") or "") != "1" or (tbl.get("rowCnt") or "") not in ("1", "2"):
        return None
    group = figure_group(tbl)
    holder = group if group is not None else tbl
    cap = holder.find(f"{{{_HP}}}caption")
    if cap is None:
        return None
    cap_text = _caption_text(cap)
    if not FIGURE_FRAMING_RE.match(cap_text):
        return None
    first = tbl.find(f"{{{_HP}}}tr/{{{_HP}}}tc")
    pic = first.find(f".//{{{_HP}}}pic") if first is not None else None
    if pic is None or pic.find(f"{{{_HC}}}img") is None:
        return None
    return holder, cap_text


def figure_source(pic) -> tuple[str | None, str | None]:
    """``(source_path, label)`` recorded in a picture's alt text by ``write``."""
    comment = pic.find(f"{{{_HP}}}shapeComment")
    text = (comment.text or "") if comment is not None else ""
    name, _, _ = parse_alt_text(text)
    m = _LABEL_LINE_RE.search(text)
    return name, (m.group(1) if m else None)
