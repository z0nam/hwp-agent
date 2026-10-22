"""Issue #31: Markdown figures → 1×2 borderless figure tables with their own numbering."""

from __future__ import annotations

import json
import struct
import zipfile
import zlib
from pathlib import Path

from lxml import etree

from hwp_agent.ops import extract_markdown, fill_from_markdown
from hwp_agent.ops.author import (
    FigureBlock,
    TableBlock,
    _resolve_cross_refs,
    build_report,
    parse_markdown,
)
from hwp_agent.ops.figures import parse_figure_line, rebase_figure_paths
from hwp_agent.ops.template import bundled_template_path

_DEFAULT_TPL = bundled_template_path()
_HOUSE = Path("examples/house-forms/정책과제_서식.normalized.hwpx")
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_HC = "http://www.hancom.co.kr/hwpml/2011/core"


def _png(path: Path, w: int, h: int) -> Path:
    raw = b"".join(b"\x00" + b"\x80\x80\xff" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


_MD = """# 결과

지표는 {ref:fig_a}처럼 늘고, 세부는 {ref:tbl_x}, 구성은 {ref:fig_b}에 있음

![연도별 **지표** 추이](assets/a.png){#fig_a}
출처: 제주연구원
주: 2026년은 잠정치

세부 수치 {label:tbl_x}

| 연도 | 값 |
|---|---|
| 2025 | 1 |

![권역별 구성](assets/b.png){#fig_b}
"""


def _write(tmp_path: Path, template: Path = _DEFAULT_TPL, md: str = _MD, **kw):
    _png(tmp_path / "assets" / "a.png", 400, 200)
    _png(tmp_path / "assets" / "b.png", 100, 400)
    out = tmp_path / "out.hwpx"
    res = fill_from_markdown(
        template, md, output=out, chapter="Ⅳ", base_dir=tmp_path, **kw
    )
    return res, out


def _sections(out: Path):
    with zipfile.ZipFile(out) as z:
        return [
            etree.fromstring(z.read(n))
            for n in sorted(z.namelist())
            if n.startswith("Contents/section")
        ]


def _figure_tables(out: Path):
    return [
        t for s in _sections(out) for t in s.iter(f"{{{_HP}}}tbl")
        if t.find(f".//{{{_HP}}}pic") is not None and t.get("colCnt") == "1"
    ]


def _caption(tbl) -> str:
    cap = tbl.find(f"{{{_HP}}}caption")
    return "".join(t.text or "" for t in cap.iter(f"{{{_HP}}}t"))


# --------------------------------------------------------------------------- #
# parsing + numbering
# --------------------------------------------------------------------------- #
def test_parse_figure_line_and_notes() -> None:
    fig = parse_figure_line("![제목](assets/x.png){#fig_x}")
    assert fig is not None and (fig.alt, fig.path, fig.label) == ("제목", "assets/x.png", "fig_x")
    assert parse_figure_line("![](<a b.png>)").path == "a b.png"
    assert parse_figure_line("본문 ![x](y.png) 사이") is None  # inline image: not a figure

    blocks = parse_markdown("![가](a.png){#fig_a}\n출처: 기관\n주: 잠정\n\n본문\n")
    assert isinstance(blocks[0], FigureBlock)
    assert blocks[0].note == "출처: 기관\n주: 잠정"
    assert blocks[1].text == "본문"


def test_figure_sequence_is_independent_of_tables() -> None:
    blocks = parse_markdown(_MD)
    warnings: list[str] = []
    _resolve_cross_refs(blocks, "Ⅳ", warnings)
    figs = [b for b in blocks if isinstance(b, FigureBlock)]
    assert [f.display for f in figs] == ["그림 Ⅳ-1", "그림 Ⅳ-2"]
    assert blocks[1].text == "지표는 그림 Ⅳ-1처럼 늘고, 세부는 표 Ⅳ-1, 구성은 그림 Ⅳ-2에 있음"
    assert sum(isinstance(b, TableBlock) for b in blocks) == 1
    assert not warnings


def test_unresolved_figure_ref_warns_and_keeps_token() -> None:
    blocks = parse_markdown("![a](a.png){#fig_a}\n\n{ref:fig_nope} 참조\n")
    warnings: list[str] = []
    _resolve_cross_refs(blocks, None, warnings)
    assert blocks[0].display == "그림 1"
    assert blocks[1].text == "{ref:fig_nope} 참조"
    assert any("fig_nope" in w for w in warnings)


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
def test_write_builds_borderless_figure_tables(tmp_path: Path) -> None:
    res, out = _write(tmp_path)
    assert res.figures == 2
    figs = _figure_tables(out)
    assert len(figs) == 2
    assert [_caption(t) for t in figs] == ["[그림 Ⅳ-1] 연도별 지표 추이", "[그림 Ⅳ-2] 권역별 구성"]

    with zipfile.ZipFile(out) as z:
        header = etree.fromstring(z.read("Contents/header.xml"))
    fills = {b.get("id"): b for b in header.iter(f"{{{_HH}}}borderFill")}
    for t in figs:
        # one column, picture on top, note row below; numbered as a picture
        assert (t.get("rowCnt"), t.get("colCnt")) == ("2", "1")
        assert t.get("numberingType") == "PICTURE"
        assert t.find(f".//{{{_HP}}}autoNum") is None  # never advances Hangul's count
        for bf_id in {t.get("borderFillIDRef")} | {
            tc.get("borderFillIDRef") for tc in t.iter(f"{{{_HP}}}tc")
        }:
            sides = [fills[bf_id].find(f"{{{_HH}}}{s}Border") for s in
                     ("left", "right", "top", "bottom")]
            assert all(s.get("type") == "NONE" for s in sides)

    # picture fills the text width, aspect kept
    first = figs[0]
    pic_sz = first.find(f".//{{{_HP}}}pic/{{{_HP}}}sz")
    w, h = int(pic_sz.get("width")), int(pic_sz.get("height"))
    assert w == int(first.find(f"{{{_HP}}}sz").get("width"))
    assert abs(w / h - 2.0) < 0.01
    # note lines: one paragraph each in the lower cell
    note_tr = first.findall(f"{{{_HP}}}tr")[1]
    texts = [
        "".join(t.text or "" for t in p.iter(f"{{{_HP}}}t"))
        for p in note_tr.iter(f"{{{_HP}}}p")
    ]
    assert texts == ["출처: 제주연구원", "주: 2026년은 잠정치"]


def test_tall_figure_is_capped_to_the_page(tmp_path: Path) -> None:
    _, out = _write(tmp_path)
    tall = _figure_tables(out)[1]
    pic_sz = tall.find(f".//{{{_HP}}}pic/{{{_HP}}}sz")
    w, h = int(pic_sz.get("width")), int(pic_sz.get("height"))
    assert w < int(tall.find(f"{{{_HP}}}sz").get("width"))  # narrowed to fit the height
    assert abs(h / w - 4.0) < 0.01


def test_table_numbering_unaffected_by_figures(tmp_path: Path) -> None:
    _, out = _write(tmp_path)
    tables = [
        t for s in _sections(out) for t in s.iter(f"{{{_HP}}}tbl")
        if t.get("numberingType") == "TABLE" and t.find(f"{{{_HP}}}caption") is not None
    ]
    assert len(tables) == 1
    assert "세부 수치" in _caption(tables[0])


def test_template_figure_is_consumed_and_its_bindata_dropped(tmp_path: Path) -> None:
    """The {{figure_template}} paragraph is the caption reference, then removed:
    its picture (BinData/image1) never reaches the output; new images are appended."""
    _, out = _write(tmp_path)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        hpf = z.read("Contents/content.hpf").decode("utf-8")
        sec = z.read("Contents/section0.xml").decode("utf-8")
    assert "{{figure_template}}" not in sec
    assert not any(n.startswith("BinData/image1.") for n in names)
    assert 'id="image1"' not in hpf
    assert {"BinData/image2.png", "BinData/image3.png"} <= set(names)
    assert hpf.count('isEmbeded="1"') == 2
    refs = {img.get("binaryItemIDRef") for s in _sections(out) for img in s.iter(f"{{{_HC}}}img")}
    assert refs == {"image2", "image3"}


def test_caption_style_comes_from_figure_template(tmp_path: Path) -> None:
    with zipfile.ZipFile(_DEFAULT_TPL) as z:
        tpl = etree.fromstring(z.read("Contents/section0.xml"))
    ref_cap = next(
        c for c in tpl.iter(f"{{{_HP}}}caption")
        if "figure_template" in "".join(c.itertext())
    )
    want = ref_cap.find(f".//{{{_HP}}}p").get("styleIDRef")
    _, out = _write(tmp_path)
    for t in _figure_tables(out):
        assert t.find(f"{{{_HP}}}caption//{{{_HP}}}p").get("styleIDRef") == want


def test_caption_links_use_the_shared_caption_path(tmp_path: Path) -> None:
    md = "![[기관](https://example.org) 자료](assets/a.png)\n"
    res, out = _write(tmp_path, md=md)
    cap = _figure_tables(out)[0].find(f"{{{_HP}}}caption")
    assert cap.find(f".//{{{_HP}}}fieldBegin[@type='HYPERLINK']") is not None
    assert res.links == 1


def test_house_form_caption_without_table_autonum(tmp_path: Path) -> None:
    """No figure template in the house form: the caption takes the table-caption
    style, but its table auto-number is not carried over."""
    res, out = _write(tmp_path, template=_HOUSE)
    figs = _figure_tables(out)
    assert [_caption(t) for t in figs] == ["[그림 Ⅳ-1] 연도별 지표 추이", "[그림 Ⅳ-2] 권역별 구성"]
    for t in figs:
        assert t.find(f".//{{{_HP}}}autoNum") is None


def test_missing_image_keeps_figure_and_numbering(tmp_path: Path) -> None:
    md = "![없음](assets/none.png){#fig_a}\n\n![있음](assets/a.png){#fig_b}\n\n{ref:fig_b}\n"
    res, out = _write(tmp_path, md=md)
    assert res.figures == 2
    assert any("image not found" in w and "그림 Ⅳ-1" in w for w in res.warnings)
    tables = [t for s in _sections(out) for t in s.iter(f"{{{_HP}}}tbl")
              if t.get("numberingType") == "PICTURE"]
    assert [_caption(t) for t in tables] == ["[그림 Ⅳ-1] 없음", "[그림 Ⅳ-2] 있음"]
    assert "[그림 없음: assets/none.png]" in "".join(tables[0].itertext())


def test_no_figures_keeps_51_cleanup(tmp_path: Path) -> None:
    res, out = _write(tmp_path, md="# 제목\n\n본문\n")
    assert res.figures == 0
    with zipfile.ZipFile(out) as z:
        assert not any(n.startswith("BinData/") for n in z.namelist())


# --------------------------------------------------------------------------- #
# extract round trip + build
# --------------------------------------------------------------------------- #
def test_extract_round_trip(tmp_path: Path) -> None:
    _, out = _write(tmp_path)
    md = extract_markdown(out)
    assert (
        "![연도별 지표 추이](assets/a.png){#fig_a}\n출처: 제주연구원\n주: 2026년은 잠정치\n"
    ) in md
    assert "![권역별 구성](assets/b.png){#fig_b}" in md
    assert "[그림" not in md

    # and back: the extracted Markdown writes the same figures again
    (tmp_path / "rt.md").write_text(md, encoding="utf-8")
    out2 = tmp_path / "rt.hwpx"
    res = fill_from_markdown(_DEFAULT_TPL, md, output=out2, chapter="Ⅳ", base_dir=tmp_path)
    assert res.figures == 2
    assert [_caption(t) for t in _figure_tables(out2)] == [
        "[그림 Ⅳ-1] 연도별 지표 추이", "[그림 Ⅳ-2] 권역별 구성",
    ]


def test_build_reads_images_relative_to_each_part(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _png(src / "img" / "a.png", 300, 100)
    (src / "01.md").write_text("# 장\n\n![가](img/a.png){#fig_a}\n\n{ref:fig_a}\n",
                               encoding="utf-8")
    manifest = tmp_path / "report.json"
    manifest.write_text(json.dumps({
        "template": str(_DEFAULT_TPL.resolve()), "output": "out.hwpx",
        "chapter": "Ⅰ", "parts": {"body": ["src/01.md"]},
    }), encoding="utf-8")
    res = build_report(manifest)
    assert res.figures == 1
    assert not any("image not found" in w for w in res.warnings)
    md = extract_markdown(tmp_path / "out.hwpx")
    assert "![가](src/img/a.png){#fig_a}" in md


def test_rebase_leaves_unknown_paths() -> None:
    base = Path("/nonexistent-base")
    assert rebase_figure_paths("![a](x.png)", base) == "![a](x.png)"


# --------------------------------------------------------------------------- #
# review follow-ups (PR #60)
# --------------------------------------------------------------------------- #
def test_parenthesised_path_is_a_figure() -> None:
    fig = parse_figure_line("![결과](assets/plot(1).png){#fig_p}")
    assert fig is not None and fig.path == "assets/plot(1).png" and fig.label == "fig_p"


def test_gif_embeds_without_pillow(tmp_path: Path) -> None:
    gif = (
        b"GIF89a\x03\x00\x02\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        b"!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x03\x00\x02\x00\x00"
        b"\x02\x02D\x01\x00;"
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "g.gif").write_bytes(gif)
    res, out = _write(tmp_path, md="![움짤](assets/g.gif)\n")
    assert not any("image" in w for w in res.warnings)
    pic_sz = _figure_tables(out)[0].find(f".//{{{_HP}}}pic/{{{_HP}}}sz")
    assert abs(int(pic_sz.get("width")) / int(pic_sz.get("height")) - 1.5) < 0.01
    with zipfile.ZipFile(out) as z:
        assert any(n.endswith(".gif") for n in z.namelist())


def test_placeholder_figures_count_across_build_parts(tmp_path: Path) -> None:
    """A missing image in an earlier part still holds its number for later parts."""
    _png(tmp_path / "a.png", 200, 100)
    (tmp_path / "intro.md").write_text("![없음](none.png)\n", encoding="utf-8")
    (tmp_path / "body.md").write_text("# 장\n\n![있음](a.png){#fig_a}\n\n{ref:fig_a}\n",
                                      encoding="utf-8")
    manifest = tmp_path / "report.json"
    manifest.write_text(json.dumps({
        "template": str(_HOUSE.resolve()), "output": "out.hwpx", "chapter": "Ⅰ",
        "parts": {"intro": "intro.md", "body": "body.md"},
    }), encoding="utf-8")
    res = build_report(manifest)
    assert res.figures == 2
    caps = [
        _caption(t) for s in _sections(tmp_path / "out.hwpx") for t in s.iter(f"{{{_HP}}}tbl")
        if t.get("numberingType") == "PICTURE"
    ]
    assert caps == ["[그림 Ⅰ-1] 없음", "[그림 Ⅰ-2] 있음"]
