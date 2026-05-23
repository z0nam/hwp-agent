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
    # the appended heading paragraphs must carry the template's outline styles
    appended = doc.paragraphs[-4:]
    by_text = {p.text: str(p.style_id_ref) for p in appended}
    assert by_text["새 장"] == roles["HEADING_1"]
    assert by_text["새 절"] == roles["HEADING_2"]

    # heading paragraphs resolve to OUTLINE headings (auto-numbered by Hangul)
    h1 = next(p for p in appended if p.text == "새 장")
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
