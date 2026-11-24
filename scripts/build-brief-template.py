#!/usr/bin/env python3
"""Build ``examples/정책이슈브리프-template.hwpx`` from a published 정책이슈브리프 (#91).

The 정책이슈브리프 form that ``refresh-house-forms`` bakes from the institute's
``.hwp`` has its masterpages out of order (the 장 날개 of section N shows another
chapter) and example chapters in every section. A published issue brief has the
real structure, so this keeps **layout, masterpages, decoration and the institute's
cover / back-cover art** from one and throws everything the author wrote away:

* section0  cover + 인사말 + Contents page + chapter 1 (``{{ch1_title}}`` / ``{{ch1}}``)
* section1  chapter 2   (``{{ch2_title}}`` / ``{{ch2}}``)
* section2  chapters 3… (``{{ch3_title}}`` / ``{{ch3}}``) — the published brief puts
  every later chapter in this one section
* section3  참고문헌     (``{{references}}``)
* section4  back cover

Cover texts become slots (``{{volume}}`` ``{{authors}}`` ``{{title_1}}`` ``{{title_2}}``
``{{headline}}`` ``{{toc_1}}``…``{{toc_4}}`` ``{{publisher}}``); masterpage texts become
``{{running_title}}`` and ``{{wing_1}}``…``{{wing_3}}``. The cover's title and author
boxes are then reset to the institute's current form (``정책이슈브리프.hwp``,
2026-06-15; see ``COVER_FORM``), which differs from the published brief there.

Everything of the source's content is removed: all body paragraphs, tables, footnotes
and photos (their ``BinData``), the cropped screenshot the chapter ornament was cut
from (only the ornament's own pixels are kept), picture file names, document
properties, caret position and the preview. ``--trace`` prints every remaining text
node, picture comment, metadata value and ``BinData`` entry so the result can be
checked for leftovers before it is committed.

Needs macOS ``sips`` (crops the ornament). Usage::

    scripts/build-brief-template.py SOURCE.hwpx [-o examples/정책이슈브리프-template.hwpx]
    scripts/build-brief-template.py --trace examples/정책이슈브리프-template.hwpx
"""
from __future__ import annotations

import argparse
import copy
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
OPF = "http://www.idpf.org/2007/opf/"
PX = 75  # HWPUNIT per image pixel

#: the cover's title / author boxes as the institute's current form has them
#: (웍스 기관 서식 ``정책이슈브리프.hwp``, 2026-06-15). The published brief centres the
#: title in a wider box and sets the authors left-aligned in a taller box; the form sets
#: the title left (JUSTIFY) in a narrower box and the authors right-aligned on one line
#: under it. Geometry in HWPUNIT; ``align`` / ``break_non_latin`` go into a fresh paraPr,
#: ``ratio`` / ``spacing`` into a fresh charPr (the published shapes are left untouched).
COVER_FORM = {
    "authors": {"width": 18160, "height": 2108, "horz": 35433, "vert": 31144, "align": "RIGHT"},
    "title": {
        "width": 36633, "height": 6362, "horz": 8855, "vert": 21234,
        "align": "JUSTIFY", "break_non_latin": "KEEP_WORD", "ratio": "95", "spacing": "-5",
    },
}


def hp(tag: str) -> str:
    return f"{{{HP}}}{tag}"


def text_nodes(el) -> list:
    return list(el.iter(hp("t")))


def set_text(p, value: str) -> None:
    """Put *value* in the first ``<hp:t>`` of *p*; empty every other one."""
    ts = text_nodes(p)
    if not ts:
        run = p.find(hp("run")) if p.find(hp("run")) is not None else etree.SubElement(p, hp("run"))
        ts = [etree.SubElement(run, hp("t"))]
    ts[0].text = value
    for c in list(ts[0]):
        ts[0].remove(c)
    for t in ts[1:]:
        t.text = ""
        for c in list(t):
            t.remove(c)


def marker_para(src, token: str):
    """A copy of the plain paragraph *src* holding only *token*."""
    p = copy.deepcopy(src)
    for r in p.findall(hp("run"))[1:]:
        p.remove(r)
    run = p.find(hp("run"))
    for c in list(run):
        run.remove(c)
    etree.SubElement(run, hp("t")).text = token
    for lsa in p.findall(hp("linesegarray")):
        p.remove(lsa)
    return p


def plain_source(root):
    for p in root.findall(hp("p")):
        if p.find(".//" + hp("secPr")) is None and not list(p.iter(hp("pic"), hp("tbl"), hp("rect"))):
            return p
    raise RuntimeError("no plain paragraph to clone")


def keep_first(root, n: int, token: str, src) -> None:
    """Keep the first *n* top-level paragraphs, drop the rest, then add *token*."""
    paras = root.findall(hp("p"))
    for p in paras[n:]:
        root.remove(p)
    root.append(marker_para(src, token))


def png(w: int, h: int, rgb=(255, 255, 255)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def pic_image_id(pic) -> str | None:
    img = pic.find(f"{{{HC}}}img")
    return img.get("binaryItemIDRef") if img is not None else None


def retarget_pic(pic, w_px: int, h_px: int) -> None:
    """Point *pic* at an image that is exactly the clipped region (w×h px)."""
    ow, oh = w_px * PX, h_px * PX
    cur = pic.find(hp("curSz"))
    cw, ch = int(cur.get("width")), int(cur.get("height"))
    # the source placed a window of a big image with a negative offset / translation;
    # the new image *is* the window, so there is nothing to shift
    off = pic.find(hp("offset"))
    off.set("x", "0")
    off.set("y", "0")
    pic.find(hp("orgSz")).set("width", str(ow))
    pic.find(hp("orgSz")).set("height", str(oh))
    ri = pic.find(hp("renderingInfo"))
    for tag, vals in (
        ("transMatrix", dict(e1="1", e2="0", e3="0", e4="0", e5="1", e6="0")),
        ("scaMatrix", dict(e1=f"{cw / ow:.6f}", e2="0", e3="0", e4="0", e5=f"{ch / oh:.6f}", e6="0")),
        ("rotMatrix", dict(e1="1", e2="0", e3="0", e4="0", e5="1", e6="0")),
    ):
        el = ri.find(f"{{{HC}}}{tag}")
        for k, v in vals.items():
            el.set(k, v)
    rect = pic.find(hp("imgRect"))
    for pt, (x, y) in zip(("pt0", "pt1", "pt2", "pt3"), ((0, 0), (ow, 0), (ow, oh), (0, oh))):
        e = rect.find(f"{{{HC}}}{pt}")
        e.set("x", str(x))
        e.set("y", str(y))
    clip = pic.find(hp("imgClip"))
    clip.set("left", "0")
    clip.set("right", str(ow))
    clip.set("top", "0")
    clip.set("bottom", str(oh))
    dim = pic.find(hp("imgDim"))
    dim.set("dimwidth", str(ow))
    dim.set("dimheight", str(oh))


def scrub_comment(pic, name: str, w_px: int | None = None, h_px: int | None = None) -> None:
    cm = pic.find(hp("shapeComment"))
    if cm is None:
        return
    lines = ["그림입니다.", f"원본 그림의 이름: {name}"]
    if w_px and h_px:
        lines.append(f"원본 그림의 크기: 가로 {w_px}pixel, 세로 {h_px}pixel")
    cm.text = "\n".join(lines)


#: the published brief's table title is a plain 바탕 paragraph (paraPr 1 / charPr 1)
TABLE_CAPTION = ("0", "1", "1")  # styleIDRef, paraPrIDRef, charPrIDRef


def make_sample_table_para(src) -> "etree._Element":
    """A paragraph holding a blanked copy of the published data table, captioned
    ``<표 {{chapter_number}}-N> {{table_template:sample}}`` (autoNum), for the fill to
    copy and then drop. Cells keep their borderFill / paraPr / charPr; all text goes."""
    host, tbl_src = src
    host = copy.deepcopy(host)
    tbl = next(host.iter(hp("tbl")))
    for t in tbl.iter(hp("t")):
        t.text = ""
        for c in list(t):
            t.remove(c)
    for tag in ("pic", "footNote", "endNote", "memo"):
        if list(tbl.iter(hp(tag))):
            raise RuntimeError(f"sample table contains <{tag}>")
    tbl.set("id", "1700000001")
    # cells without their own margin (hasMargin=0) store -1 as 4294967295; resolve it
    # from the table's inMargin so no sentinel is carried into generated tables
    inm = tbl.find(hp("inMargin"))
    for mg in tbl.iter(hp("cellMargin")):
        for k in ("left", "right", "top", "bottom"):
            if int(mg.get(k, "0")) >= 2**31:
                mg.set(k, inm.get(k, "0") if inm is not None else "0")
    for ru in host.iter(hp("run")):  # nothing but the table itself may carry text
        for t in ru.findall(hp("t")):
            t.text = ""
    width = tbl.find(hp("sz")).get("width")
    style, pr, ch = TABLE_CAPTION
    cap = etree.fromstring(
        f'<hp:caption xmlns:hp="{HP}" side="TOP" fullSz="0" width="8504" gap="850" '
        f'lastWidth="{width}"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        f'vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
        f'hasTextRef="0" hasNumRef="0"><hp:p id="2147483648" paraPrIDRef="{pr}" '
        f'styleIDRef="{style}" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{ch}"><hp:t>&lt;표 {{{{chapter_number}}}}-</hp:t><hp:ctrl>'
        f'<hp:autoNum num="1" numType="TABLE"><hp:autoNumFormat type="DIGIT" userChar="" '
        f'prefixChar="" suffixChar="" supscript="0"/></hp:autoNum></hp:ctrl>'
        f'<hp:t>&gt; {{{{table_template:sample}}}}</hp:t></hp:run></hp:p></hp:subList></hp:caption>'
    )
    tbl.find(hp("tr")).addprevious(cap)  # the caption sits after inMargin, before the rows
    return host


def reshape_box(rect, width: int, height: int, horz: int, vert: int, **_) -> None:
    """Resize / move a cover text box, keeping its drawing matrix consistent."""
    sz, cur = rect.find(hp("sz")), rect.find(hp("curSz"))
    ow, oh = int(cur.get("width")), int(cur.get("height"))
    for el in (sz, cur):
        el.set("width", str(width))
        el.set("height", str(height))
    rot = rect.find(hp("rotationInfo"))
    rot.set("centerX", str(width // 2))
    rot.set("centerY", str(height // 2))
    sca = rect.find(".//{%s}scaMatrix" % HC)
    sca.set("e1", f"{float(sca.get('e1')) * width / ow:.6g}")
    sca.set("e5", f"{float(sca.get('e5')) * height / oh:.6g}")
    pos = rect.find(hp("pos"))
    pos.set("horzOffset", str(horz))
    pos.set("vertOffset", str(vert))


def restyle_cover(header, boxes: dict) -> None:
    """Give each cover box's paragraphs fresh paraPr / charPr copies set as the form has them.

    ``boxes`` maps a ``COVER_FORM`` key to its rect. Copies (new ids) rather than edits,
    so a shape the published brief also uses elsewhere stays as it was.
    """
    hh = "{http://www.hancom.co.kr/hwpml/2011/head}"

    def fresh(group: str, tag: str, ref: str) -> etree._Element:
        props = header.find(f".//{hh}{group}")
        items = props.findall(f"{hh}{tag}")
        src = next(x for x in items if x.get("id") == ref)
        new = copy.deepcopy(src)
        new.set("id", str(max(int(x.get("id")) for x in items) + 1))
        props.append(new)
        props.set("itemCnt", str(len(items) + 1))
        return new

    for key, rect in boxes.items():
        spec = COVER_FORM[key]
        ps = rect.findall(f"{hp('drawText')}/{hp('subList')}/{hp('p')}")
        para = fresh("paraProperties", "paraPr", ps[0].get("paraPrIDRef"))
        para.find(f"{hh}align").set("horizontal", spec["align"])
        if "break_non_latin" in spec:
            para.find(f"{hh}breakSetting").set("breakNonLatinWord", spec["break_non_latin"])
        char = None
        if "ratio" in spec:
            char = fresh("charProperties", "charPr", ps[0].find(hp("run")).get("charPrIDRef"))
            for tag, val in (("ratio", spec["ratio"]), ("spacing", spec["spacing"])):
                el = char.find(f"{hh}{tag}")
                for lang in el.attrib:
                    el.set(lang, val)
        for p in ps:
            p.set("paraPrIDRef", para.get("id"))
            if char is not None:
                for r in p.findall(hp("run")):
                    r.set("charPrIDRef", char.get("id"))


def build(src: Path, dst: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    zin = zipfile.ZipFile(src)
    names = zin.namelist()
    load = lambda n: etree.fromstring(zin.read(n))  # noqa: E731
    sec = {i: load(f"Contents/section{i}.xml") for i in range(5)}
    mp = {i: load(f"Contents/masterpage{i}.xml") for i in range(5)}
    out: dict[str, bytes] = {}

    # the first captionless data table of chapter 2 (the published brief titles its
    # tables with a separate paragraph above, so none of them carries a caption element)
    sample_src = next(
        t for t in sec[1].iter(hp("tbl"))
        if t.find(hp("caption")) is None and t.get("numberingType") == "TABLE"
        and not list(t.iter(hp("pic")))
    )
    sample_src = copy.deepcopy(sample_src)
    sample_host = copy.deepcopy(next(
        p for p in sec[1].findall(hp("p")) if p.find(".//" + hp("tbl")) is not None
        and p.find(".//" + hp("tbl")).get("numberingType") == "TABLE"
        and p.find(".//" + hp("tbl")).find(hp("caption")) is None
    ))
    sample_src = (sample_host, sample_src)

    # ----- section0: cover, 인사말, Contents, chapter 1 --------------------------
    s0 = sec[0]
    rects = [r for r in s0.iter(hp("rect")) if text_nodes(r)]
    texts = [[(t.text or "") for t in text_nodes(r)] for r in rects]
    if len(rects) != 13 or not texts[0][0].startswith("VOL."):
        raise RuntimeError("section0 does not look like a 정책이슈브리프 cover")
    # the empty-text rects (backgrounds) are skipped above; map the text ones by order
    vol, authors, title, greeting, headline = rects[0], rects[1], rects[2], rects[3], rects[4]
    contents = rects[5:]
    labels = [r for r in contents if re.match(r"^[ⅠⅡⅢⅣⅤ]\.\s*$", "".join(t.text or "" for t in text_nodes(r)))]
    titles = [r for r in contents if r not in labels]
    if len(titles) != 4 or len(labels) != 4:
        raise RuntimeError(f"unexpected Contents boxes: {len(titles)} titles, {len(labels)} labels")
    vts = text_nodes(vol)
    vts[1].text = "{{volume}}"
    for t in vts[2:]:
        t.text = ""
    ps = list(authors.iter(hp("p")))
    set_text(ps[0], "{{authors}}")
    for extra in ps[1:]:
        extra.getparent().remove(extra)
    tp = next(title.iter(hp("p")))
    second = copy.deepcopy(tp)
    set_text(tp, "{{title_1}}")
    set_text(second, "{{title_2}}")
    tp.addnext(second)
    set_text(next(headline.iter(hp("p"))), "{{headline}}")
    for key, rect in (("authors", authors), ("title", title)):
        reshape_box(rect, **COVER_FORM[key])
    header = load("Contents/header.xml")
    restyle_cover(header, {"authors": authors, "title": title})
    for k, r in enumerate(titles, start=1):
        set_text(next(r.iter(hp("p"))), "{{toc_%d}}" % k if k > 1 else "{{toc_1:들어가며}}")

    paras = s0.findall(hp("p"))
    head = next(i for i, p in enumerate(paras) if p.get("styleIDRef") == "1")
    if "Ⅰ" not in "".join(t.text or "" for t in text_nodes(paras[head])):
        raise RuntimeError("chapter 1 heading not found")
    set_text(paras[head], "{{ch1_title}}")
    assert paras[head + 1].find(".//" + hp("pic")) is not None, "ornament under chapter 1 title"
    simple = plain_source(sec[1]) if False else None
    simple = next(
        p for p in paras if p.get("styleIDRef") == "0" and not list(p.iter(hp("pic"), hp("tbl"), hp("rect"), hp("secPr")))
    )
    for p in paras[head + 2:]:
        s0.remove(p)
    s0.append(marker_para(simple, "{{ch1}}"))

    # ----- sections 1-3: chapter openers, references -----------------------------
    def chapter_section(i: int, token_title: str, token_body: str) -> None:
        root = sec[i]
        paras = root.findall(hp("p"))
        assert paras[0].get("styleIDRef") == "1" and paras[0].find(".//" + hp("secPr")) is not None
        set_text(paras[0], token_title)
        assert paras[1].find(".//" + hp("pic")) is not None, f"ornament in section{i}"
        keep_first(root, 2, token_body, simple)

    chapter_section(1, "{{ch2_title}}", "{{ch2}}")
    chapter_section(2, "{{ch3_title}}", "{{ch3}}")
    s3 = sec[3]
    p3 = s3.findall(hp("p"))
    assert p3[2].find(".//" + hp("pic")) is not None
    keep_first(s3, 3, "{{references}}", simple)

    # ----- sample table: the published data-table format (#115) ------------------------
    # captured *before* the chapter bodies are thrown away; blanked and given the
    # ``<표 Ⅱ-N> {{table_template:sample}}`` caption so ``write`` copies its borders /
    # zone shading / note row and drops it again at fill time
    sample_para = make_sample_table_para(sample_src)
    sec1_paras = sec[1].findall(hp("p"))
    sec1_paras[-1].addprevious(sample_para)  # in front of the {{ch2}} marker paragraph

    # ----- section4: back cover ---------------------------------------------------
    for r in sec[4].iter(hp("rect")):
        for t in text_nodes(r):
            if t.text and "vol." in t.text:
                t.text = re.sub(r"vol\.\S*", "vol.{{volume}}", t.text)
            elif t.text and t.text.strip() and t.getparent() is not None:
                pass
        ts = [t for t in text_nodes(r)]
        for a, b in zip(ts, ts[1:]):
            if (a.text or "").strip() == "발행인:":
                b.text = " {{publisher}}"

    # ----- masterpages: running title + chapter wings ------------------------------
    def direct_ts(p) -> list:  # this paragraph's own <hp:t> (not those of nested paragraphs)
        return [t for r in p.findall(hp("run")) for t in r.findall(hp("t"))]

    for k, token in ((0, "{{running_title}}"), (1, "{{wing_1}}"), (2, "{{wing_2}}"), (3, "{{wing_3}}")):
        # the wing text lives in the *innermost* paragraph holding it; the outer paragraph
        # of a table cell merely contains it, so matching on subtree text hit the wrong one
        p = next(p for p in mp[k].iter(hp("p")) if "".join(t.text or "" for t in direct_ts(p)).strip())
        ts = direct_ts(p)
        ts[0].text = token
        for t in ts[1:]:
            t.text = ""
    # masterpage4 keeps its fixed "참고문헌" wing

    # ----- pictures: scrub names; crop the ornament out of its screenshot ---------
    deco_png: bytes | None = None
    keep_bin: dict[str, bytes] = {}
    tmp = Path(tempfile.mkdtemp(prefix="brief-tpl-"))
    for i, root in {**{f"s{k}": v for k, v in sec.items()}}.items():
        for pic in root.iter(hp("pic")):
            iid = pic_image_id(pic)
            clip = pic.find(hp("imgClip"))
            dim = pic.find(hp("imgDim"))
            if iid in ("image3", "image12"):
                # a cropped page screenshot: keep only the ornament's own pixels
                l, r_, t_, b_ = (int(clip.get(k)) for k in ("left", "right", "top", "bottom"))
                x, y, w, h = round(l / PX), round(t_ / PX), round((r_ - l) / PX), round((b_ - t_) / PX)
                srcpng = tmp / f"{iid}.png"
                srcpng.write_bytes(zin.read(f"BinData/{iid}.png"))
                dstpng = tmp / f"{iid}.crop.png"
                subprocess.run(
                    ["sips", "-c", str(h), str(w), "--cropOffset", str(y), str(x),
                     str(srcpng), "--out", str(dstpng)],
                    check=True, capture_output=True,
                )
                keep_bin[iid] = dstpng.read_bytes()
                retarget_pic(pic, w, h)
                scrub_comment(pic, "ornament.png", w, h)
            else:
                ow = int(dim.get("dimwidth")) // PX
                oh = int(dim.get("dimheight")) // PX
                scrub_comment(pic, {"image1": "cover.jpg", "image2": "contents.jpg", "image13": "back-cover.jpg"}.get(iid or "", "image"), ow, oh)

    # ----- serialise parts ---------------------------------------------------------
    def dump(name: str, root) -> bytes:
        orig = zin.read(name)
        decl = orig[: orig.index(b"?>") + 2]
        return decl + etree.tostring(root, encoding="UTF-8", xml_declaration=False)

    for i, root in sec.items():
        out[f"Contents/section{i}.xml"] = dump(f"Contents/section{i}.xml", root)
    for i, root in mp.items():
        out[f"Contents/masterpage{i}.xml"] = dump(f"Contents/masterpage{i}.xml", root)
    out["Contents/header.xml"] = dump("Contents/header.xml", header)

    # which BinData is still referenced?
    used: set[str] = set()
    for blob in out.values():
        used |= set(re.findall(rb'binaryItemIDRef="([^"]+)"', blob))
    used = {u.decode() for u in used}
    hdr = zin.read("Contents/header.xml").decode("utf-8")
    used |= set(re.findall(r'binaryItemIDRef="([^"]+)"', hdr))
    drop = {n for n in names if n.startswith("BinData/") and Path(n).stem not in used}
    for iid, data in keep_bin.items():
        out[f"BinData/{iid}.png"] = data

    # ----- package metadata, preview, caret ----------------------------------------
    hpf = zin.read("Contents/content.hpf").decode("utf-8")
    for n in drop:
        hpf = re.sub(r'<opf:item id="%s" href="%s"[^>]*/>' % (re.escape(Path(n).stem), re.escape(n)), "", hpf)

    def meta(name: str, value: str) -> None:
        nonlocal hpf
        hpf = re.sub(
            r'(<opf:meta name="%s" content="text")(?:/>|>.*?</opf:meta>)' % name,
            lambda m: m.group(1) + (f">{value}</opf:meta>" if value else "/>"),
            hpf, flags=re.S,
        )

    hpf = re.sub(r"<opf:title>.*?</opf:title>", "<opf:title>정책이슈브리프 서식</opf:title>", hpf, flags=re.S)
    for name in ("creator", "subject", "description", "lastsaveby", "keyword"):
        meta(name, "")
    meta("CreatedDate", "2026-11-18T00:00:00Z")
    meta("ModifiedDate", "2026-11-18T00:00:00Z")
    meta("date", "")
    out["Contents/content.hpf"] = hpf.encode("utf-8")
    settings = zin.read("settings.xml").decode("utf-8")
    out["settings.xml"] = re.sub(r'(<ha:CaretPosition listIDRef=")\d+(" paraIDRef=")\d+(" pos=")\d+', r"\g<1>0\g<2>0\g<3>0", settings).encode("utf-8")
    out["Preview/PrvText.txt"] = "정책이슈브리프 서식".encode("utf-8")
    out["Preview/PrvImage.png"] = png(8, 8)

    with zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            if info.filename in drop:
                continue
            zout.writestr(info, out.get(info.filename, zin.read(info.filename)))
    print(f"wrote {dst}  (dropped {len(drop)} BinData: {sorted(Path(n).name for n in drop)})")
    declare_roles(dst)
    repoint_bullet_styles(dst)
    from hwp_agent.ops import neutralize_uncaptioned_numbering

    n = neutralize_uncaptioned_numbering(dst)  # decoration stays out of 그림·표 차례 (#101)
    print(f"un-numbered {n} decorative object(s)")


#: styleName → the paragraph shape the published brief really uses for it: 본문강조 text is
#: set with the ▪ bullet shape and 참고 with a hanging indent, while the style's own
#: default shape has neither — without this a ``write``-n bullet comes out bare
BULLET_SHAPES = {"본문강조": "23", "참고": "24"}


def repoint_bullet_styles(path: Path) -> None:
    import zipfile as _zf

    from hwp_agent.ops.container import _rewrite_zip_preserving

    with _zf.ZipFile(path) as z:
        header = z.read("Contents/header.xml").decode("utf-8")
    for name, pr in BULLET_SHAPES.items():
        header, n = re.subn(
            r'(<hh:style id="\d+"[^>]*name="%s"[^>]*paraPrIDRef=")\d+(")' % re.escape(name),
            lambda m, pr=pr: m.group(1) + pr + m.group(2),
            header,
        )
        if n != 1:
            raise RuntimeError(f"style {name!r} not found exactly once")
    tmp = path.with_suffix(".style.tmp")
    _rewrite_zip_preserving(path, tmp, {"Contents/header.xml": header.encode("utf-8")})
    tmp.replace(path)
    print(f"bullet styles use the published paragraph shapes: {BULLET_SHAPES}")


#: styleName → ROLE, the same mapping the house-form override declares for the brief
#: (사용자 지정 2026-08-19: 제목=로마자 장 헤딩, 본문강조=1불릿, 참고=2불릿)
ROLES = {"제목": "HEADING_1", "본문강조": "BULLET_1", "참고": "BULLET_2"}


def declare_roles(path: Path) -> None:
    """Mark the brief's styles with their ``AI:<ROLE>`` names so ``write`` finds them."""
    from hwp_agent.ops import apply_style_roles

    tmp = path.with_suffix(".roles.tmp")
    actions = apply_style_roles(path, ROLES, tmp)
    tmp.replace(path)
    print(f"declared {len(actions)} style role(s): {ROLES}")


def trace(path: Path) -> None:
    """Print every piece of text / metadata left in the template."""
    z = zipfile.ZipFile(path)
    print("== BinData")
    for i in z.infolist():
        if i.filename.startswith("BinData/") or i.filename.startswith("Preview/"):
            print(f"  {i.filename}  {i.file_size}")
    for n in sorted(z.namelist()):
        if re.search(r"(section|masterpage)\d+\.xml$", n):
            root = etree.fromstring(z.read(n))
            print(f"== {n}")
            for t in root.iter(hp("t")):
                s = (t.text or "") + "".join(c.tail or "" for c in t)
                if s.strip():
                    print("  t:", repr(s))
            for cm in root.iter(hp("shapeComment")):
                print("  comment:", repr(cm.text))
            for tag in ("footNote", "endNote", "tbl", "memo"):
                k = len(list(root.iter(hp(tag))))
                if k:
                    print(f"  {tag}: {k}")
    print("== header.xml text nodes")
    h = etree.fromstring(z.read("Contents/header.xml"))
    for el in h.iter():
        if isinstance(el.tag, str) and (el.text or "").strip() and not el.tag.endswith(("paraHead",)):
            print("  ", el.tag.split("}")[1], repr(el.text.strip())[:80])
    print("== content.hpf metadata")
    print(re.sub(r"<opf:manifest>.*?</opf:manifest>|<opf:spine>.*?</opf:spine>", "", z.read("Contents/content.hpf").decode(), flags=re.S)[-900:])
    print("== settings.xml caret:", re.findall(r"<ha:CaretPosition[^>]*>", z.read("settings.xml").decode()))
    print("== Preview/PrvText.txt:", repr(z.read("Preview/PrvText.txt").decode()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=Path("examples/정책이슈브리프-template.hwpx"))
    ap.add_argument("--trace", action="store_true", help="dump leftovers of SOURCE (a built template) and exit")
    args = ap.parse_args()
    if args.trace:
        trace(args.source)
        return 0
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
