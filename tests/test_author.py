"""Tests for hwp_agent.ops.author (Markdown -> styled fill)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwp_agent.ops import (
    TableBlock,
    fill_from_markdown,
    inline_segments,
    parse_markdown,
    plain_text,
    role_map,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TYPE1 = REPO_ROOT / "tests" / "fixtures" / "sample_hwpx.hwpx"


def test_parse_markdown_blocks() -> None:
    md = "# 제목\n\n본문 한 줄.\n이어지는 줄.\n\n- 항목 하나\n- 항목 둘\n\n## 소제목\n"
    blocks = parse_markdown(md)
    kinds = [(b.kind, b.level) for b in blocks]
    assert kinds == [
        ("heading", 1),
        ("paragraph", 0),  # the two consecutive lines merge into one paragraph
        ("bullet", 1),
        ("bullet", 1),
        ("heading", 2),
    ]
    assert blocks[1].text == "본문 한 줄. 이어지는 줄."


def test_parse_markdown_keeps_inline_markers() -> None:
    (block,) = parse_markdown("**굵게** 그리고 *기울임*")
    assert block.text == "**굵게** 그리고 *기울임*"  # markers kept for run-building
    assert plain_text(block.text) == "굵게 그리고 기울임"


def test_inline_segments_splits_emphasis() -> None:
    segs = inline_segments("앞 **굵게** 중간 *기울임* 끝")
    assert [(s.text, s.bold, s.italic) for s in segs] == [
        ("앞 ", False, False),
        ("굵게", True, False),
        (" 중간 ", False, False),
        ("기울임", False, True),
        (" 끝", False, False),
    ]
    # no emphasis -> single plain segment
    assert [(s.text, s.bold, s.italic) for s in inline_segments("그냥")] == [
        ("그냥", False, False)
    ]


def test_parse_markdown_detects_pipe_table() -> None:
    md = "본문\n\n| 항목 | 값 |\n|------|:--:|\n| 인구 | 67만 |\n\n다음 a | b 아님\n"
    blocks = parse_markdown(md)
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    t = tables[0]
    assert t.n_rows == 2 and t.n_cols == 2
    assert t.rows == [["항목", "값"], ["인구", "67만"]]
    assert t.aligns == ["left", "center"]
    # the trailing "a | b" line (no delimiter) is NOT a table
    assert any(b.__class__.__name__ == "Block" and "아님" in b.text for b in blocks)


def test_parse_thematic_break() -> None:
    """`---` / `***` / `___` on their own line are horizontal-rule blocks."""
    blocks = parse_markdown("위\n\n---\n\n***\n\n___\n\n- - -\n\n아래\n")
    assert [(b.kind, b.text) for b in blocks] == [
        ("paragraph", "위"),
        ("rule", ""),
        ("rule", ""),
        ("rule", ""),
        ("rule", ""),
        ("paragraph", "아래"),
    ]
    # `--` (only two) is not a rule; a pipe delimiter is still a table, not a rule
    assert [b.kind for b in parse_markdown("--\n")] == ["paragraph"]


def test_strip_manual_heading_numbering() -> None:
    """Manual section numbers in headings are dropped (template auto-numbers)."""
    md = (
        "## 부록 A: 프로젝트\n\n### A-1 세부\n\n## 1.1 배경\n\n"
        "## 제2장 총론\n\n# 부록\n\n## 2024 결산\n\n## A형 조사\n"
    )
    headings = [(b.level, b.text) for b in parse_markdown(md) if b.kind == "heading"]
    assert headings == [
        (2, "프로젝트"),  # "부록 A:" stripped
        (3, "세부"),  # "A-1" stripped
        (2, "배경"),  # "1.1" stripped
        (2, "총론"),  # "제2장" stripped
        (1, "부록"),  # bare "부록" would empty the heading -> kept
        (2, "2024 결산"),  # a year (no delimiter) is not numbering -> kept
        (2, "A형 조사"),  # a single letter without a sub-number -> kept
    ]


def test_cli_missing_input_file_is_friendly(capsys) -> None:
    """A missing input path is a clear message + exit 2, not a Python traceback."""
    from hwp_agent.cli.main import main

    rc = main(["write", "/no/such/file.md", "--template", str(TYPE1)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "file.md" in err and "Traceback" not in err


# --------------------------------------------------------------------------- #
# cross-references — {label:id} on a table, {ref:id} anywhere
# --------------------------------------------------------------------------- #
def test_cross_ref_resolves_to_table_autonum() -> None:
    """`{label:id}` on a table caption + `{ref:id}` in prose resolves to 표 ch-N."""
    from hwp_agent.ops.author import _resolve_cross_refs

    blocks = parse_markdown(
        "신뢰도 등급 {label:csv_grade}\n"
        "| 등급 | 정의 |\n|------|------|\n| A | 실측 |\n\n"
        "본문에서 {ref:csv_grade}를 참고.\n\n"
        "사업 일람 {label:csv_list}\n"
        "| 코드 | 명 |\n|------|----|\n| P09 | 새연교 |\n\n"
        "또 {ref:csv_list} 참고.\n"
    )
    warnings: list[str] = []
    _resolve_cross_refs(blocks, "부록", warnings)

    tables = [b for b in blocks if isinstance(b, TableBlock)]
    paras = [b.text for b in blocks if not isinstance(b, TableBlock)]
    # label tokens stripped from rendered captions
    assert tables[0].caption == "신뢰도 등급"
    assert tables[1].caption == "사업 일람"
    # refs in prose resolved to autonum text
    assert "표 부록-1" in paras[0] and "{ref:" not in paras[0]
    assert "표 부록-2" in paras[1] and "{ref:" not in paras[1]
    assert warnings == []


def test_cross_ref_unresolved_keeps_token_and_warns() -> None:
    """An unknown `{ref:id}` stays as-is so the issue is visible, plus a warning."""
    from hwp_agent.ops.author import _resolve_cross_refs

    blocks = parse_markdown("본문에서 {ref:없는것}와 {ref:다른것}를 참조.\n")
    warnings: list[str] = []
    _resolve_cross_refs(blocks, "부록", warnings)

    assert "{ref:없는것}" in blocks[0].text and "{ref:다른것}" in blocks[0].text
    assert any("unresolved" in w for w in warnings)
    assert "없는것" in warnings[0] and "다른것" in warnings[0]


def test_cross_ref_in_table_cell_resolves() -> None:
    """`{ref:id}` inside a table cell is resolved too (the matching table use case)."""
    from hwp_agent.ops.author import _resolve_cross_refs

    blocks = parse_markdown(
        "등급표 {label:grade}\n| 등급 | 정의 |\n|---|---|\n| A | 실측 |\n\n"
        "매칭표\n| 이름 | 참조 |\n|---|---|\n| 등급 | {ref:grade} |\n"
    )
    warnings: list[str] = []
    _resolve_cross_refs(blocks, "부록", warnings)
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    # the matching table's second row, second cell now reads "표 부록-1"
    assert tables[1].rows[1][1] == "표 부록-1"


def test_cross_ref_without_chapter_falls_back_to_bare_number() -> None:
    """No chapter set → "표 N" (no chapter prefix), still works."""
    from hwp_agent.ops.author import _resolve_cross_refs

    blocks = parse_markdown(
        "표제 {label:x}\n| a | b |\n|---|---|\n| 1 | 2 |\n\n{ref:x} 참조.\n"
    )
    warnings: list[str] = []
    _resolve_cross_refs(blocks, None, warnings)
    paras = [b.text for b in blocks if not isinstance(b, TableBlock)]
    assert "표 1" in paras[0]


def test_cross_ref_duplicate_label_warns_first_wins() -> None:
    """Same id declared on two tables → warning; first declaration wins."""
    from hwp_agent.ops.author import _resolve_cross_refs

    blocks = parse_markdown(
        "첫 표 {label:dup}\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "둘째 표 {label:dup}\n| c | d |\n|---|---|\n| 3 | 4 |\n\n"
        "{ref:dup} 본다.\n"
    )
    warnings: list[str] = []
    _resolve_cross_refs(blocks, "부록", warnings)
    paras = [b.text for b in blocks if not isinstance(b, TableBlock)]
    assert "표 부록-1" in paras[0]  # first declaration wins (not -2)
    assert any("duplicate" in w and "dup" in w for w in warnings)


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_thematic_break_becomes_horizontal_line(tmp_path: Path) -> None:
    """A `---` renders as a real <hp:line> shape, not literal '---' text."""
    from hwpx.document import HwpxDocument

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

    def n_lines(doc) -> int:
        return sum(1 for s in doc.sections for _ in s.element.iter(f"{HP}line"))

    before = n_lines(HwpxDocument.open(str(TYPE1)))
    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(TYPE1, "위 문단\n\n---\n\n아래 문단\n", output=out)
    assert result.placed == 3

    doc = HwpxDocument.open(str(out))  # round-trips
    assert "---" not in "".join(p.text or "" for p in doc.paragraphs)  # not literal text
    assert n_lines(doc) == before + 1  # exactly one horizontal line added
    # the added line spans a real width and has a non-zero box height
    added = [
        ln
        for s in doc.sections
        for ln in s.element.iter(f"{HP}line")
        if (sz := ln.find(f"{HP}sz")) is not None and int(sz.get("width", "0")) > 14400
    ]
    assert added and int(added[0].find(f"{HP}sz").get("height")) >= 1


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_table_copies_template_format(tmp_path: Path) -> None:
    import zipfile

    from hwpx.document import HwpxDocument

    out = tmp_path / "out.hwpx"
    md = "| 항목 | 값 |\n|------|----|\n| 인구 | **67만** |\n"
    result = fill_from_markdown(TYPE1, md, output=out)
    assert result.placed == 1

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    doc = HwpxDocument.open(str(out))
    tbls = [t for s in doc.sections for t in s.element.iter(f"{HP}tbl")]
    ref_bf = tbls[0].get("borderFillIDRef")  # reference = first table
    gen = tbls[-1]  # our generated table is appended last
    # generated table reuses the reference table's border-fill (house style)
    assert gen.get("borderFillIDRef") == ref_bf
    assert gen.get("rowCnt") == "2" and gen.get("colCnt") == "2"
    cells = ["".join(t.text or "" for t in tc.iter(f"{HP}t")) for tc in gen.iter(f"{HP}tc")]
    assert cells == ["항목", "값", "인구", "67만"]  # **67만** -> run, plain text "67만"

    # no replacement chars introduced
    with zipfile.ZipFile(out) as zf:
        body = b"".join(
            zf.read(n) for n in zf.namelist() if n.startswith("Contents/section")
        )
    assert b"\xef\xbf\xbd" not in body


_TABLE_TEMPLATE = REPO_ROOT / "tests" / "fixtures" / "authored_table_template.hwpx"


@pytest.mark.skipif(
    not _TABLE_TEMPLATE.is_file(), reason="caption-marked table template not present"
)
def test_table_format_uses_caption_designated_reference(tmp_path: Path) -> None:
    """A table whose caption holds a {{table…}} token is the format reference."""
    from hwpx.document import HwpxDocument

    from hwp_agent.ops.author import _find_reference_table

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    src = HwpxDocument.open(str(_TABLE_TEMPLATE))
    ref_el, _section, designated = _find_reference_table(src)
    assert designated is True  # picked the marked table, not the first decorative one
    ref_bf = ref_el.get("borderFillIDRef")

    out = tmp_path / "out.hwpx"
    md = "| 코드 | 사업명 |\n|------|--------|\n| P99 | 테스트 |\n"
    fill_from_markdown(_TABLE_TEMPLATE, md, output=out)

    doc = HwpxDocument.open(str(out))
    tbls = [t for s in doc.sections for t in s.element.iter(f"{HP}tbl")]
    assert tbls[-1].get("borderFillIDRef") == ref_bf  # generated copies marked format
    # the designation token is cleaned from the caption
    texts = "".join(t.text or "" for s in doc.sections for t in s.element.iter(f"{HP}t"))
    assert "{{table" not in texts


_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HH = "{http://www.hancom.co.kr/hwpml/2011/head}"


def _find_authored_table(doc, marker: str):
    for sec in doc.sections:
        for tbl in sec.element.iter(f"{_HP}tbl"):
            if marker in "".join(t.text or "" for t in tbl.iter(f"{_HP}t")):
                return tbl, sec
    return None, None


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 fixture not present")
def test_table_width_fits_text_column(tmp_path: Path) -> None:
    """Generated table rows are scaled to the section's text-column width (item E)."""
    from hwpx.document import HwpxDocument

    from hwp_agent.ops.author import _text_width

    out = tmp_path / "out.hwpx"
    md = "표 제목\n| 콜에이 | 콜비 | 콜시 |\n|---|---|---|\n| 엑스 | 와이 | 제트 |\n"
    fill_from_markdown(TYPE1, md, output=out)
    doc = HwpxDocument.open(str(out))
    tbl, sec = _find_authored_table(doc, "콜에이")
    assert tbl is not None
    tw = _text_width(sec)
    assert tw and tw > 0
    for tr in tbl.findall(f"{_HP}tr"):
        widths = [
            int(tc.find(f"{_HP}cellSz").get("width"))
            for tc in tr.findall(f"{_HP}tc")
            if tc.find(f"{_HP}cellSz") is not None
        ]
        if widths:
            assert sum(widths) == tw  # each row fills the text column exactly


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 fixture not present")
def test_missing_table_token_warns(tmp_path: Path) -> None:
    """Authoring tables without a {{table…}} token (or pattern) warns (item A)."""
    md = "표\n| 에이 | 비 |\n|---|---|\n| 1 | 2 |\n"
    res = fill_from_markdown(TYPE1, md, output=tmp_path / "o.hwpx")
    assert any("{{table" in w for w in res.warnings)
    if _TABLE_TEMPLATE.is_file():  # a token-marked template should not warn
        res2 = fill_from_markdown(_TABLE_TEMPLATE, md, output=tmp_path / "o2.hwpx")
        assert not res2.warnings


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 fixture not present")
def test_authored_heading_has_linesegarray(tmp_path: Path) -> None:
    """Authored headings carry a linesegarray so Hangul keeps them as headings (item C)."""
    from hwpx.document import HwpxDocument

    from hwp_agent.ops.author import _lineseg_index

    src = HwpxDocument.open(str(TYPE1))
    h1_style = role_map(str(TYPE1)).get("HEADING_1")
    if h1_style is None or str(h1_style) not in _lineseg_index(src):
        pytest.skip("template has no same-style heading linesegarray to clone")

    out = tmp_path / "out.hwpx"
    fill_from_markdown(TYPE1, "# 단일 헤딩 줄\n\n본문\n", output=out)
    doc = HwpxDocument.open(str(out))
    heading = next(p for p in doc.paragraphs if (p.text or "").strip() == "단일 헤딩 줄")
    lsa = heading.element.find(f"{_HP}linesegarray")
    assert lsa is not None and lsa.find(f"{_HP}lineseg") is not None


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 fixture not present")
def test_inline_bold_preserves_base_size(tmp_path: Path) -> None:
    """Inline **bold** keeps the base font size, not a 20pt charPr (item F)."""
    from hwpx.document import HwpxDocument

    out = tmp_path / "out.hwpx"
    fill_from_markdown(TYPE1, "여기 **굵은말** 있음\n", output=out)
    doc = HwpxDocument.open(str(out))
    cps = doc._root._headers[0]._char_properties_element(create=False)

    def height(cid):
        el = cps.find(f"{_HH}charPr[@id='{cid}']")
        return el.get("height") if el is not None else None

    base_char = doc.style(role_map(str(TYPE1))["BODY"]).char_pr_id_ref
    bold_run = None
    for p in doc.paragraphs:
        if "굵은말" in (p.text or ""):
            for run in p.element.iter(f"{_HP}run"):
                if "굵은말" in "".join(t.text or "" for t in run.iter(f"{_HP}t")):
                    bold_run = run
    assert bold_run is not None
    cid = bold_run.get("charPrIDRef")
    assert cps.find(f"{_HH}charPr[@id='{cid}']").find(f"{_HH}bold") is not None
    assert height(cid) == height(str(base_char))  # size preserved (no 20pt jump)


def test_strip_table_token_keeps_caption_frame() -> None:
    import xml.etree.ElementTree as ET

    from hwp_agent.ops.author import _caption_text, _strip_table_token

    HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    # caption "<표 II-" + (autonum) + "> {{table_template}}" — the closing ">"
    # shares a text node with the token and must survive stripping.
    tbl = ET.fromstring(
        f'<hp:tbl xmlns:hp="{HP}"><hp:caption><hp:subList><hp:p><hp:run>'
        f"<hp:t>&lt;표 II-</hp:t></hp:run><hp:run>"
        f"<hp:t>&gt; {{{{table_template}}}}</hp:t></hp:run></hp:p></hp:subList>"
        f"</hp:caption></hp:tbl>"
    )
    _strip_table_token(tbl)
    assert _caption_text(tbl) == "<표 II->"  # token gone, closing ">" kept


@pytest.mark.skipif(
    not _TABLE_TEMPLATE.is_file(), reason="caption-marked table template not present"
)
def test_generated_header_row_is_marked_and_repeats(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    out = tmp_path / "out.hwpx"
    md = "| 코드 | 사업명 |\n|--|--|\n| P01 | 가 |\n| P02 | 나 |\n"
    fill_from_markdown(_TABLE_TEMPLATE, md, output=out)

    doc = HwpxDocument.open(str(out))
    gen = [t for s in doc.sections for t in s.element.iter(f"{HP}tbl")][-1]
    rows = gen.findall(f"{HP}tr")
    # header row cells carry header="1" (so the header repeats on page breaks);
    # body rows are header="0".
    assert all(tc.get("header") == "1" for tc in rows[0].findall(f"{HP}tc"))
    assert all(tc.get("header") == "0" for tc in rows[1].findall(f"{HP}tc"))
    assert gen.get("repeatHeader") == "1"


def test_parse_table_caption_and_note() -> None:
    md = (
        "표 제목입니다\n"
        "| 코드 | 값 |\n|--|--|\n| P01 | 1 |\n"
        "출처) 제주연구원\n"
        "주) 단위 백만원\n"
    )
    (table,) = [b for b in parse_markdown(md) if isinstance(b, TableBlock)]
    assert table.caption == "표 제목입니다"
    assert table.note == "출처) 제주연구원\n주) 단위 백만원"


@pytest.mark.skipif(
    not _TABLE_TEMPLATE.is_file(), reason="caption-marked table template not present"
)
def test_generated_table_caption_and_note(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    out = tmp_path / "out.hwpx"
    md = "축제사업 현황\n| 코드 | 값 |\n|--|--|\n| P01 | 1 |\n출처) 제주연구원, 2025.\n"
    fill_from_markdown(_TABLE_TEMPLATE, md, output=out)

    doc = HwpxDocument.open(str(out))
    gen = [t for s in doc.sections for t in s.element.iter(f"{HP}tbl")][-1]
    cap = gen.find(f"{HP}caption")
    assert cap is not None
    cap_text = "".join(t.text or "" for t in cap.iter(f"{HP}t"))
    assert "축제사업 현황" in cap_text and "{{table" not in cap_text
    # caption uses the table-title style copied from the reference
    assert cap.find(f"{HP}subList").find(f"{HP}p").get("styleIDRef") is not None
    # the note row carries the 출처 line
    note_text = "".join(t.text or "" for t in gen.findall(f"{HP}tr")[-1].iter(f"{HP}t"))
    assert "제주연구원" in note_text


@pytest.mark.skipif(
    not _TABLE_TEMPLATE.is_file(), reason="caption-marked table template not present"
)
def test_caption_chapter_number_and_merged_note(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    out = tmp_path / "out.hwpx"
    # an authored chapter heading bumps the chapter count used in the caption
    md = "# 새 장\n\n현황표\n| 코드 | 값 |\n|--|--|\n| P01 | 1 |\n출처) 제주연구원\n"
    fill_from_markdown(_TABLE_TEMPLATE, md, output=out)

    doc = HwpxDocument.open(str(out))
    gen = [t for s in doc.sections for t in s.element.iter(f"{HP}tbl")][-1]
    cap_text = "".join(t.text or "" for t in gen.find(f"{HP}caption").iter(f"{HP}t"))
    assert "{{chapter" not in cap_text  # placeholder substituted with a number
    assert "현황표" in cap_text

    note_row = gen.findall(f"{HP}tr")[-1]
    assert len(note_row.findall(f"{HP}tc")) == 1  # note row merged into one cell
    span = note_row.find(f"{HP}tc").find(f"{HP}cellSpan")
    assert span is not None and int(span.get("colSpan")) >= 2
    assert "제주연구원" in "".join(t.text or "" for t in note_row.iter(f"{HP}t"))


@pytest.mark.skipif(
    not _TABLE_TEMPLATE.is_file(), reason="caption-marked table template not present"
)
def test_explicit_chapter_label_in_caption(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

    def caption(path) -> str:
        doc = HwpxDocument.open(str(path))
        gen = [t for s in doc.sections for t in s.element.iter(f"{HP}tbl")][-1]
        return "".join(t.text or "" for t in gen.find(f"{HP}caption").iter(f"{HP}t"))

    md = "현황표\n| 코드 | 값 |\n|--|--|\n| P01 | 1 |\n"
    out = tmp_path / "out.hwpx"
    # explicit chapter wins (number or alpha label, e.g. an appendix "가")
    fill_from_markdown(_TABLE_TEMPLATE, md, output=out, chapter="가")
    assert "가-" in caption(out)
    fill_from_markdown(_TABLE_TEMPLATE, md, output=out, chapter=7)
    assert "7-" in caption(out)


@pytest.mark.skipif(not TYPE1.exists(), reason="sample fixture not present")
def test_instructions_cli_emits_directions_and_slots(capsys) -> None:
    import json

    from hwp_agent.cli.main import build_parser

    args = build_parser().parse_args(["instructions", str(TYPE1), "--json"])
    assert args.func(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"instructions", "slots"}
    assert isinstance(data["instructions"], list)
    assert isinstance(data["slots"], list)


def test_clone_caption_inline_forced_chapter_wins() -> None:
    import xml.etree.ElementTree as ET

    from hwp_agent.ops.author import _clone_caption

    HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    tbl = ET.fromstring(
        f'<hp:tbl xmlns:hp="{HP}"><hp:caption><hp:subList><hp:p><hp:run>'
        f"<hp:t>&lt;표 </hp:t><hp:ctrl><hp:autoNum num='1' numType='TABLE'/></hp:ctrl>"
        f"<hp:t>{{{{chapter_number=3}}}}&gt; </hp:t></hp:run></hp:p></hp:subList>"
        f"</hp:caption></hp:tbl>"
    )
    # inline {{chapter_number=3}} forces "3" even when an explicit chapter is given
    clone = _clone_caption(tbl, "제목", "9")
    text = "".join(t.text or "" for t in clone.iter(f"{{{HP}}}t"))
    assert text == "<표 3> 제목"


def test_clone_caption_replaces_chapter_token_and_title() -> None:
    import xml.etree.ElementTree as ET

    from hwp_agent.ops.author import _clone_caption

    HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    # caption "[표 I-" + autoNum + "] 참여 전문가 개요" wrapped in a table element;
    # literal "I" (no {{chapter_number}}) and an existing sample title.
    tbl = ET.fromstring(
        f'<hp:tbl xmlns:hp="{HP}"><hp:caption><hp:subList><hp:p><hp:run>'
        f"<hp:t>[표 I-</hp:t><hp:ctrl><hp:autoNum num='1' numType='TABLE'/></hp:ctrl>"
        f"<hp:t>] 참여 전문가 개요</hp:t></hp:run></hp:p></hp:subList></hp:caption></hp:tbl>"
    )
    clone = _clone_caption(tbl, "제주 우주산업 여건 분석", "Ⅲ")
    text = "".join(t.text or "" for t in clone.iter(f"{{{HP}}}t"))
    assert text == "[표 Ⅲ-] 제주 우주산업 여건 분석"  # chapter replaced, title replaced
    assert clone.find(f".//{{{HP}}}autoNum") is not None  # table auto-number kept


def test_parse_markdown_ordered_vs_bullet() -> None:
    blocks = parse_markdown("1. 첫째\n2. 둘째\n\n- 불릿\n")
    assert [(b.kind, b.level) for b in blocks] == [
        ("ordered", 1),
        ("ordered", 1),
        ("bullet", 1),
    ]


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_ordered_list_uses_numbered_outline_style(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    from hwp_agent.ops import role_map

    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(TYPE1, "1. 첫 단계\n2. 둘째 단계\n", output=out)
    assert result.unmapped_roles == []  # ordered mapped to a numbered style

    doc = HwpxDocument.open(str(out))
    item = next(p for p in doc.paragraphs if p.text == "첫 단계")
    # the ordered item resolves to an OUTLINE (auto-numbered) style, not Body
    heading = doc.paragraph_property(item.para_pr_id_ref).heading
    assert heading.type == "OUTLINE"
    assert str(item.style_id_ref) != role_map(TYPE1)["BODY"]


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_from_markdown_applies_outline_styles(tmp_path: Path) -> None:
    md = "# 새 장\n\n본문 문단.\n\n## 새 절\n\n- 불릿 항목\n"
    out = tmp_path / "authored.hwpx"
    result = fill_from_markdown(TYPE1, md, output=out)
    assert result.placed == 4
    assert result.unmapped_roles == []

    from hwpx.document import HwpxDocument

    roles = role_map(TYPE1)
    doc = HwpxDocument.open(str(out))
    # look up by text (a contextual spacer paragraph now sits before "새 절", so the
    # authored headings aren't simply the last N paragraphs)
    by_text = {(p.text or "").strip(): p for p in doc.paragraphs if (p.text or "").strip()}
    # the appended heading paragraphs must carry the template's outline styles
    assert str(by_text["새 장"].style_id_ref) == roles["HEADING_1"]
    assert str(by_text["새 절"].style_id_ref) == roles["HEADING_2"]

    # heading paragraphs resolve to OUTLINE headings (auto-numbered by Hangul)
    h1 = by_text["새 장"]
    heading = doc.paragraph_property(h1.para_pr_id_ref).heading
    assert heading.type == "OUTLINE" and heading.level == 0

    # output reopens with no replacement chars (non-BMP guard)
    import zipfile

    with zipfile.ZipFile(out) as zf:
        body = b"".join(
            zf.read(n) for n in zf.namelist() if n.startswith("Contents/section")
        )
    assert b"\xef\xbf\xbd" not in body


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_inserts_at_body_marker(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    # build a template with a {{body}} marker as the 4th paragraph of section 0
    tmpl = tmp_path / "tmpl.hwpx"
    doc = HwpxDocument.open(str(TYPE1))
    sec = doc.sections[0]
    marker = sec.add_paragraph("{{body}}", style_id_ref=0, para_pr_id_ref=0)
    el = marker.element
    el.getparent().remove(el)
    sec.paragraphs[3].element.addprevious(el)
    doc.save_to_path(str(tmpl))

    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(tmpl, "# 삽입장\n\n본문.\n", output=out)
    assert result.inserted_at_marker is True

    filled = HwpxDocument.open(str(out))
    texts = [p.text for p in filled.sections[0].paragraphs]
    assert "{{body}}" not in "".join(texts)  # marker consumed
    # authored heading sits at the marker position (index 3), not appended at the end
    assert "삽입장" in texts[3]


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_inserts_at_appendix_marker(tmp_path: Path) -> None:
    """{{appendix}} is an insertion marker too, and is consumed on fill."""
    from hwpx.document import HwpxDocument

    tmpl = tmp_path / "tmpl.hwpx"
    doc = HwpxDocument.open(str(TYPE1))
    sec = doc.sections[0]
    marker = sec.add_paragraph("{{appendix}}", style_id_ref=0, para_pr_id_ref=0)
    el = marker.element
    el.getparent().remove(el)
    sec.paragraphs[3].element.addprevious(el)
    doc.save_to_path(str(tmpl))

    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(tmpl, "# 부록 A\n\n부록 본문.\n", output=out)
    assert result.inserted_at_marker is True

    filled = HwpxDocument.open(str(out))
    texts = [p.text for p in filled.sections[0].paragraphs]
    assert "{{appendix}}" not in "".join(texts)  # marker consumed
    assert "부록 A" in texts[3]  # authored content sits at the marker position


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_preserves_section_opener_secpr(tmp_path: Path) -> None:
    """A marker on a section-opening paragraph keeps the 구역 boundary intact.

    A dedicated empty appendix section is a single paragraph that both opens the
    section (carries ``<hp:secPr>``) and holds ``{{appendix}}``. Deleting the marker
    outright would drop the secPr and collapse the section into the previous one;
    the secPr-run must be transplanted onto the first authored paragraph.
    """
    from hwpx.document import HwpxDocument

    HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

    def count_secpr(doc) -> int:
        return sum(
            1
            for s in doc.sections
            for p in s.paragraphs
            if p.element.find(f"{HP}run/{HP}secPr") is not None
        )

    doc = HwpxDocument.open(str(TYPE1))
    opener = next(
        (
            p
            for s in doc.sections
            for p in s.paragraphs
            if p.element.find(f"{HP}run/{HP}secPr") is not None
        ),
        None,
    )
    if opener is None:
        pytest.skip("fixture has no secPr-bearing section opener")
    opener.add_run("{{appendix}}")  # turn the section opener into the marker
    tmpl = tmp_path / "tmpl.hwpx"
    doc.save_to_path(str(tmpl))
    before = count_secpr(HwpxDocument.open(str(tmpl)))

    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(tmpl, "# 부록 A\n\n부록 본문.\n", output=out)
    assert result.inserted_at_marker is True

    filled = HwpxDocument.open(str(out))
    all_text = "".join(p.text or "" for p in filled.paragraphs)
    assert "{{appendix}}" not in all_text  # marker consumed
    assert "부록 A" in all_text
    # secPr survived the marker deletion (old bug dropped it -> before - 1)
    assert count_secpr(filled) == before


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_inline_bold_becomes_distinct_run(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    out = tmp_path / "out.hwpx"
    fill_from_markdown(TYPE1, "보통 **굵게** 보통\n", output=out)

    doc = HwpxDocument.open(str(out))
    para = next(p for p in doc.paragraphs if "굵게" in (p.text or ""))
    runs = para.runs
    assert len(runs) == 3  # "보통 " | "굵게" | " 보통"
    bold_run = next(r for r in runs if r.text == "굵게")
    plain_run = next(r for r in runs if r.text.strip() == "보통")
    # the bold span resolves to a different char style than the plain spans
    assert bold_run.char_pr_id_ref != plain_run.char_pr_id_ref
    assert plain_text("보통 **굵게** 보통") == "보통 굵게 보통"


# --------------------------------------------------------------------------- #
# table format zones — header vs body style banding
# --------------------------------------------------------------------------- #
def _mk_tbl(rows: list[tuple[str, int, int]]):
    """Build a minimal 3-column <hp:tbl> from (header_flag, styleID, charID) rows."""
    from xml.etree import ElementTree as ET

    hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    trs = []
    for hdr, style, char in rows:
        tcs = "".join(
            f'<hp:tc borderFillIDRef="{c + 1}" header="{hdr}">'
            f'<hp:subList vertAlign="TOP"><hp:p styleIDRef="{style}" paraPrIDRef="3">'
            f'<hp:run charPrIDRef="{char}"/></hp:p></hp:subList>'
            f'<hp:cellSz width="1000" height="500"/>'
            f'<hp:cellMargin left="0" right="0" top="0" bottom="0"/></hp:tc>'
            for c in range(3)
        )
        trs.append(f"<hp:tr>{tcs}</hp:tr>")
    xml = f'<hp:tbl xmlns:hp="{hp}" borderFillIDRef="5">{"".join(trs)}</hp:tbl>'
    return ET.fromstring(xml)


def test_table_format_distinct_header_and_body_styles() -> None:
    """Body rows keep the body style when header style differs (regression).

    The real JRI template uses 표제목(14) for the header and 표내용(8) for the body.
    The old note-row test compared each body row to the *header* style, so every
    body row was misread as a note row, body_rows went empty, and the whole table
    collapsed to the header look. Body zones must carry the body style, not 14.
    """
    from hwp_agent.ops.author import _table_format

    fmt = _table_format(_mk_tbl([("1", 14, 34), ("0", 8, 41), ("0", 8, 41), ("0", 8, 41)]))
    assert fmt.header.style == "14" and fmt.header.char == "34"
    assert fmt.body.style == "8" and fmt.body.char == "41"
    assert fmt.first_body.style == "8" and fmt.last.style == "8"
    assert fmt.note is None  # every body row shares a style -> no note row


def test_table_format_detects_trailing_note_row() -> None:
    """A trailing row whose style differs from the body style is the note row."""
    from hwp_agent.ops.author import _table_format

    fmt = _table_format(_mk_tbl([("1", 14, 34), ("0", 8, 41), ("0", 8, 41), ("0", 15, 33)]))
    assert fmt.body.style == "8"  # body rows unaffected by the note row
    assert fmt.note is not None and fmt.note.style == "15"


# --------------------------------------------------------------------------- #
# contextual heading spacing — a blank paragraph above a heading unless it hugs
# its parent (a heading directly under a shallower one, with no author blank line)
# --------------------------------------------------------------------------- #
_SPACING_MD = (
    "# 장제목1\n## 절제목1\n### 소절제목1\n* 내용내용\n\n"
    "### 소절제목2\n* 내용내용\n\n## 절제목2\n### 소절제목2-1\n* 내용내용\n"
)


def test_parse_markdown_tracks_blank_before() -> None:
    """Blank lines preceding a block are counted (drives contextual heading spacing)."""
    bb = {b.text: b.blank_before for b in parse_markdown(_SPACING_MD) if b.kind == "heading"}
    assert bb["장제목1"] == 0
    assert bb["절제목1"] == 0 and bb["소절제목1"] == 0  # hug their parent (no blank)
    assert bb["소절제목2"] == 1 and bb["절제목2"] == 1  # an author blank precedes each
    assert bb["소절제목2-1"] == 0  # hugs 절제목2


def _para_before(paras, text):
    idx = next(i for i, p in enumerate(paras) if (p.text or "").strip() == text)
    return paras[idx - 1]


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_heading_spacing_inserts_contextual_gaps(tmp_path: Path) -> None:
    """A sized blank paragraph is inserted above non-hugging headings only."""
    from hwpx.document import HwpxDocument

    before_n = len(list(HwpxDocument.open(str(TYPE1)).paragraphs))
    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(TYPE1, _SPACING_MD, output=out)
    assert result.placed == 9  # 6 headings + 3 bullets; spacer paragraphs not counted

    doc = HwpxDocument.open(str(out))
    paras = list(doc.paragraphs)
    # exactly two spacers added: none before the first heading, none before the three
    # hugging child headings (절제목1 / 소절제목1 / 소절제목2-1)
    assert len(paras) - before_n == result.placed + 2

    # a blank paragraph sits above the sibling / section-return headings…
    assert (_para_before(paras, "소절제목2").text or "").strip() == ""
    assert (_para_before(paras, "절제목2").text or "").strip() == ""
    # …and the hugging headings follow their parent directly, no blank between
    assert (_para_before(paras, "절제목1").text or "").strip() == "장제목1"
    assert (_para_before(paras, "소절제목1").text or "").strip() == "절제목1"
    assert (_para_before(paras, "소절제목2-1").text or "").strip() == "절제목2"

    # the gap is sized to the following heading: its run carries that heading's char,
    # so a ## gap is taller than a ### gap
    cps = doc._root._headers[0]._char_properties_element(create=False)

    def height(cid):
        return int(cps.find(f"{_HH}charPr[@id='{cid}']").get("height"))

    def run_char(p):
        return p.element.find(f"{_HP}run").get("charPrIDRef")

    gap2 = run_char(_para_before(paras, "절제목2"))  # before a ## heading
    gap3 = run_char(_para_before(paras, "소절제목2"))  # before a ### heading
    head2 = run_char(next(p for p in paras if (p.text or "").strip() == "절제목2"))
    head3 = run_char(next(p for p in paras if (p.text or "").strip() == "소절제목2"))
    assert gap2 == head2 and gap3 == head3  # gap height tracks the heading's font
    assert height(gap2) > height(gap3)  # ## gap taller than ### gap


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_heading_spacing_packed_tight_matches_spaced(tmp_path: Path) -> None:
    """Tightly-packed Markdown (no blank lines) yields the same gaps as the spaced form."""
    from hwpx.document import HwpxDocument

    tight = _SPACING_MD.replace("\n\n", "\n")  # drop the author blank lines
    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(TYPE1, tight, output=out)
    assert result.placed == 9

    paras = list(HwpxDocument.open(str(out)).paragraphs)
    # the structural rule still gaps the sibling / section headings…
    assert (_para_before(paras, "소절제목2").text or "").strip() == ""
    assert (_para_before(paras, "절제목2").text or "").strip() == ""
    # …and still hugs the direct children
    assert (_para_before(paras, "소절제목1").text or "").strip() == "절제목1"
    assert (_para_before(paras, "소절제목2-1").text or "").strip() == "절제목2"


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_heading_spacing_author_blank_forces_gap(tmp_path: Path) -> None:
    """An author blank line before a would-be-hugging child heading forces a gap."""
    from hwpx.document import HwpxDocument

    def hugs(md: str) -> bool:
        out = tmp_path / "o.hwpx"
        fill_from_markdown(TYPE1, md, output=out)
        paras = list(HwpxDocument.open(str(out)).paragraphs)
        return (_para_before(paras, "자식").text or "").strip() == "부모"

    assert hugs("# 부모\n## 자식\n") is True  # direct child, tight → hugs (no gap)
    assert hugs("# 부모\n\n## 자식\n") is False  # author blank line → gap honored
