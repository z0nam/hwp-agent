"""Issue #119: a figure's caption sits on a group of its pictures (``hp:container``),
so Hangul's 그림 차례 collects it — a captioned figure table it does not.

All manuscripts here are synthetic; images are tiny generated PNGs.
"""

from __future__ import annotations

import json
import struct
import zipfile
import zlib
from pathlib import Path

from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops import build_report, extract_markdown, fill_from_markdown
from hwp_agent.ops.figures import GROUP_COMMENT, figure_group
from hwp_agent.ops.images import list_images, replace_image
from hwp_agent.ops.template import bundled_template_path
from hwp_agent.ops.toc import figure_toc_command

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "examples" / "정책과제-template.hwpx"
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HC = "{http://www.hancom.co.kr/hwpml/2011/core}"

BODY = """# Ⅰ. 서론

## 1. 배경

본문 문장임

![넓은 가상 그림](wide.png){#fig_wide}
자료: 가상 자료

![세로로 긴 가상 그림](tall.png)

# Ⅱ. 분석

## 1. 현황

![두 장 나란히 가상 그림](wide.png) ![](sq.png){#fig_pair}
주) 좌: 가, 우: 나
"""


def _png(path: Path, w: int, h: int) -> Path:
    raw = b"".join(b"\x00" + b"\x80\x80\xff" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def _images(d: Path) -> None:
    _png(d / "wide.png", 40, 25)
    _png(d / "tall.png", 20, 45)
    _png(d / "sq.png", 25, 25)


def _manifest(d: Path, body: str = BODY) -> Path:
    _images(d)
    (d / "body.md").write_text(body, encoding="utf-8")
    m = {"template": str(GOLDEN), "output": "out.hwpx", "parts": {"body": "body.md"}}
    (d / "m.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    return d / "m.json"


def _sections(path: Path) -> dict[str, etree._Element]:
    with zipfile.ZipFile(path) as z:
        return {n: etree.fromstring(z.read(n)) for n in sorted(z.namelist())
                if n.startswith("Contents/section") and n.endswith(".xml")}


def _figure_tables(path: Path) -> list:
    return [t for root in _sections(path).values() for t in root.iter(f"{_HP}tbl")
            if figure_group(t) is not None]


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(f"{_HP}t"))


def _write(tmp_path: Path, md: str) -> Path:
    _images(tmp_path)
    out = tmp_path / "o.hwpx"
    fill_from_markdown(bundled_template_path(), md, output=out, base_dir=tmp_path)
    return out


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #
def test_caption_goes_on_a_text_wide_picture_group(tmp_path: Path) -> None:
    out = _write(tmp_path, "![한 장](wide.png){#fig_a}\n\n![두 장](wide.png) ![](sq.png)\n")
    tables = _figure_tables(out)
    assert len(tables) == 2
    for tbl in tables:
        tw = int(tbl.find(f"{_HP}sz").get("width"))
        group = figure_group(tbl)
        # the table is no longer numbered nor captioned — the group is
        assert tbl.get("numberingType") == "NONE"
        assert tbl.find(f"{_HP}caption") is None
        assert group.get("numberingType") == "PICTURE"
        assert group.find(f"{_HP}pos").get("treatAsChar") == "1"
        # group (and its caption) as wide as the text, held there by an invisible line
        assert int(group.find(f"{_HP}sz").get("width")) == tw
        cap = group.find(f"{_HP}caption")
        assert cap.get("width") == cap.get("lastWidth") == str(tw)
        line = group.find(f"{_HP}line")
        assert line.find(f"{_HP}lineShape").get("style") == "NONE"
        assert line.find(f"{_HC}endPt").get("x") == str(tw)
        # element order as Hangul saves a group: …, shapeComment, caption
        tags = [etree.QName(c).localname for c in group]
        assert tags[-5:] == ["sz", "pos", "outMargin", "shapeComment", "caption"]
        # children as Hangul writes them: id 0, level 1, image right after renderingInfo,
        # no own size/position/alt text
        for pic in group.findall(f"{_HP}pic"):
            assert (pic.get("id"), pic.get("groupLevel"), pic.get("numberingType")) == (
                "0", "1", "NONE")
            kids = [etree.QName(c).localname for c in pic]
            assert kids[kids.index("renderingInfo") + 1] == "img"
            assert not {"sz", "pos", "outMargin", "shapeComment"} & set(kids)
    # the pictures stay centred side by side; the spacer runs between them are gone
    pair = figure_group(tables[1])
    pics = pair.findall(f"{_HP}pic")
    xs = [int(p.find(f"{_HP}offset").get("x")) for p in pics]
    ws = [int(p.find(f"{_HP}curSz").get("width")) for p in pics]
    tw = int(pair.find(f"{_HP}sz").get("width"))
    assert xs[1] == xs[0] + ws[0] + 400
    assert abs((tw - (xs[1] + ws[1])) - xs[0]) <= 1
    para = pair.getparent().getparent()
    assert [_text(r) for r in para.findall(f"{_HP}run")] == [_text(pair.getparent())]


def test_group_keeps_each_pictures_source_for_extract(tmp_path: Path) -> None:
    out = _write(tmp_path, "![두 장](wide.png) ![](sq.png){#fig_pair}\n주) 좌: 가\n")
    assert [p.origname for p in list_images(out)] == ["wide.png", "sq.png"]
    comment = figure_group(_figure_tables(out)[0]).find(f"{_HP}shapeComment").text
    assert comment.startswith(GROUP_COMMENT)
    assert "원본 그림의 이름: wide.png" in comment and "원본 그림의 이름: sq.png" in comment
    assert "그림 참조 id: fig_pair" in comment
    md = extract_markdown(out)
    assert "![두 장](wide.png) ![](sq.png){#fig_pair}" in md
    assert "주) 좌: 가" in md


def test_extract_round_trip_of_a_build(tmp_path: Path) -> None:
    build_report(_manifest(tmp_path), literal_numbering=True)
    md = extract_markdown(tmp_path / "out.hwpx")
    assert "![넓은 가상 그림](wide.png){#fig_wide}\n자료: 가상 자료" in md
    assert "![세로로 긴 가상 그림](tall.png)" in md
    assert "![두 장 나란히 가상 그림](wide.png) ![](sq.png){#fig_pair}" in md


def test_missing_image_keeps_the_caption_on_the_table(tmp_path: Path) -> None:
    out = _write(tmp_path, "![없음](none.png)\n\n![있음](wide.png)\n")
    roots = _sections(out).values()
    placeholder = [t for r in roots for t in r.iter(f"{_HP}tbl")
                   if t.get("numberingType") == "PICTURE"]
    assert len(placeholder) == 1 and figure_group(placeholder[0]) is None
    assert _text(placeholder[0].find(f"{_HP}caption")).startswith("[그림 1]")
    assert len(_figure_tables(out)) == 1


# --------------------------------------------------------------------------- #
# 그림 차례
# --------------------------------------------------------------------------- #
def _toc_root(path: Path):
    return _sections(path)["Contents/section1.xml"]


def _figure_field(root) -> tuple[list[str], list[str]]:
    """(그림 차례 field commands, link targets of its entries)."""
    paras = root.findall(f"{_HP}p")
    head = next(i for i, p in enumerate(paras) if _text(p).strip() == "그림 차례")
    cmds, targets = [], []
    for p in paras[head + 1:]:
        for sp in p.iter(f"{_HP}stringParam"):
            if sp.get("name") != "Command":
                continue
            if sp.text.startswith("TableOfContents"):
                cmds.append(sp.text)
            elif sp.text.startswith("?#"):
                targets.append(sp.text[2:].split(";")[0])
    return cmds, targets


def test_figure_list_is_a_hangul_field_linked_to_the_groups(tmp_path: Path) -> None:
    res = build_report(_manifest(tmp_path), literal_numbering=True)
    assert res.toc["figures"] == 3 and res.toc["figure_field"] is True
    out = tmp_path / "out.hwpx"
    cmds, targets = _figure_field(_toc_root(out))
    assert cmds == [figure_toc_command(int(cmds[0].split("ContentsLeader:int:")[1][0]))]
    assert "ContentsMake:uint:4 " in cmds[0]
    groups = [figure_group(t).get("id") for t in _figure_tables(out)]
    assert targets == groups


def test_figure_field_also_in_default_numbering(tmp_path: Path) -> None:
    """Captions don't depend on outline paragraphs, so the 그림 차례 is a field in
    the default mode too (like the 표 차례)."""
    res = build_report(_manifest(tmp_path))
    assert res.toc["field"] is False
    assert res.toc["figure_field"] is True


def test_placeholder_figure_keeps_the_figure_list_static(tmp_path: Path) -> None:
    """A figure without a picture keeps its caption on the table, which Hangul does
    not collect — a field would drop it on refresh, so the list stays static."""
    res = build_report(_manifest(tmp_path, BODY + "\n![없는 그림](none.png)\n"))
    assert res.toc["figures"] == 4 and res.toc["figure_field"] is False
    cmds, _ = _figure_field(_toc_root(tmp_path / "out.hwpx"))
    assert cmds == []


def test_cli_build_keeps_group_numbering_and_says_how_pages_fill(
    tmp_path: Path, capsys
) -> None:
    """#109's neutralize pass leaves captioned groups numbered; the CLI tells the
    user the 그림 차례 fills on refresh."""
    rc = main(["build", str(_manifest(tmp_path)), "--literal-numbering"])
    assert rc == 0
    tables = _figure_tables(tmp_path / "out.hwpx")
    assert [figure_group(t).get("numberingType") for t in tables] == ["PICTURE"] * 3
    out = capsys.readouterr().out
    assert "차례·그림 차례는 한글에서 차례 새로 고침" in out


# --------------------------------------------------------------------------- #
# hwp-agent image on a grouped picture
# --------------------------------------------------------------------------- #
def test_image_list_and_replace_inside_a_group(tmp_path: Path) -> None:
    out = _write(tmp_path, "![한 장](wide.png)\n")
    pics = list_images(out)
    assert len(pics) == 1 and pics[0].caption.startswith("[그림 1] 한 장")
    # metadata read from the group: source in its comment, size from curSz
    assert (pics[0].origname, pics[0].px_w, pics[0].px_h) == ("wide.png", 40, 25)
    assert pics[0].extent_w > 0 and pics[0].extent_h > 0
    before = figure_group(_figure_tables(out)[0]).find(f"{_HP}pic")
    w0 = int(before.find(f"{_HP}curSz").get("width"))
    new = _png(tmp_path / "new.png", 40, 40)
    res = replace_image(out, image=new, ref=pics[0].ref, output=tmp_path / "r.hwpx")
    assert res.replaced == 1
    pic = figure_group(_figure_tables(tmp_path / "r.hwpx")[0]).find(f"{_HP}pic")
    cur = pic.find(f"{_HP}curSz")
    assert int(cur.get("width")) == w0 and int(cur.get("height")) == w0  # square now
    assert pic.find(f"{_HP}orgSz").get("width") == str(40 * 75)
    sca = pic.find(f"{_HP}renderingInfo/{_HC}scaMatrix")
    assert abs(float(sca.get("e1")) * 40 * 75 - w0) < 2
    assert pic.find(f"{_HP}sz") is None  # still a group child
    assert any("묶음" in w for w in res.warnings)
