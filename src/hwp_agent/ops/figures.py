"""Figure building blocks for ``write`` (issue #31).

A Markdown figure ``![alt](assets/x.png){#fig_X}`` becomes a **1-column, 2-row,
borderless table**: the top cell holds the picture, the bottom cell the 출처/주
lines written directly under the image line. The table carries a ``[그림 Ⅳ-2] alt``
caption and is numbered in its own figure sequence, never as a table (see
:func:`hwp_agent.ops.author._resolve_cross_refs`).

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
from dataclasses import dataclass
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
        \(\s*(?P<path><[^>]+>|[^)\s]+)(?:\s+"[^"]*")?\s*\)
        \s*(?:\{\s*\#(?P<label>[^{}\s]+)\s*\})?\s*$""",
    re.VERBOSE,
)
#: a leading ``[그림 …]`` caption framing (stripped on extract, rebuilt on write)
FIGURE_FRAMING_RE = re.compile(r"^\s*\[\s*그림\s*[^\]]*\]\s*")
#: shapeComment line carrying the figure's ``{#id}`` so ``extract`` can restore it
_LABEL_LINE = "그림 참조 id:"
_LABEL_LINE_RE = re.compile(re.escape(_LABEL_LINE) + r"\s*(\S+)")

#: formats Hangul embeds as pictures; the BinData extension used for each
_EXT = {"png": "png", "jpeg": "jpg", "gif": "gif", "bmp": "bmp", "tiff": "tif"}
_MEDIA = {"png": "image/png", "jpg": "image/jpg", "gif": "image/gif",
          "bmp": "image/bmp", "tif": "image/tiff"}


@dataclass
class FigureBlock:
    """A Markdown image line, built as a 1×2 borderless figure table."""

    alt: str  # caption title (rendered after the "[그림 …]" framing)
    path: str  # image path as written (relative to the Markdown's directory)
    label: str | None = None  # {#fig_X} anchor, if any
    note: str | None = None  # 출처/주 line(s) directly below the image line
    blank_before: int = 0
    display: str = ""  # "그림 Ⅳ-2" — set by cross-ref resolution


def parse_figure_line(line: str) -> FigureBlock | None:
    """A :class:`FigureBlock` if *line* is a standalone image line, else None."""
    m = FIGURE_LINE_RE.match(line)
    if not m:
        return None
    path = m.group("path")
    if path.startswith("<") and path.endswith(">"):
        path = path[1:-1]
    return FigureBlock(alt=m.group("alt").strip(), path=path, label=m.group("label"))


def rebase_figure_paths(markdown: str, base: Path) -> str:
    """Make relative image paths in standalone figure lines absolute under *base*.

    ``build`` concatenates several Markdown files that each name images relative to
    their own directory; rebasing each file before joining keeps those paths valid.
    A path that doesn't exist under *base* is left as written (it is then read
    relative to the manifest's folder).
    """
    out = []
    for line in markdown.split("\n"):
        m = FIGURE_LINE_RE.match(line)
        if m:
            raw = m.group("path")
            p = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
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
    bundled template's figure), except ``numberingType="NONE"``: the enclosing
    figure table carries the figure numbering. ``imgDim``/``imgClip``/``orgSz`` are the native size
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


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# header properties
# --------------------------------------------------------------------------- #
def _header(doc):
    return doc._root._headers[0] if getattr(doc._root, "_headers", None) else None


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
def figure_parts(tbl) -> tuple[object, str] | None:
    """``(pic, caption_text)`` when *tbl* is a figure table ``write`` built, else None.

    A figure table is one column, one or two rows, a picture in the first cell, and
    a caption framed ``[그림 …]`` — the shape :func:`write` emits.
    """
    if (tbl.get("colCnt") or "") != "1" or (tbl.get("rowCnt") or "") not in ("1", "2"):
        return None
    cap = tbl.find(f"{{{_HP}}}caption")
    if cap is None:
        return None
    cap_text = "".join(t.text or "" for t in cap.iter(f"{{{_HP}}}t")).strip()
    if not FIGURE_FRAMING_RE.match(cap_text):
        return None
    first = tbl.find(f"{{{_HP}}}tr/{{{_HP}}}tc")
    pic = first.find(f".//{{{_HP}}}pic") if first is not None else None
    if pic is None or pic.find(f"{{{_HC}}}img") is None:
        return None
    return pic, cap_text


def figure_source(pic) -> tuple[str | None, str | None]:
    """``(source_path, label)`` recorded in a picture's alt text by ``write``."""
    comment = pic.find(f"{{{_HP}}}shapeComment")
    text = (comment.text or "") if comment is not None else ""
    name, _, _ = parse_alt_text(text)
    m = _LABEL_LINE_RE.search(text)
    return name, (m.group(1) if m else None)
