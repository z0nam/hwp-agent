"""Inserting paragraphs into an already type-set document."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import TextEditError, find_anchors, insert_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "sample_hwpx.hwpx"
ANCHOR = "투입 예산 대비 경제적 효과"

_P_RE = re.compile(r"<hp:p\b.*?</hp:p>", re.S)
_T_RE = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)


def _paragraphs(path, section="Contents/section4.xml"):
    with zipfile.ZipFile(path) as zf:
        return _P_RE.findall(zf.read(section).decode("utf-8"))


def _text(paragraph: str) -> str:
    return "".join(_T_RE.findall(paragraph))


def _ids(paragraph: str) -> tuple[str, str, str]:
    m = re.search(r'paraPrIDRef="(\d+)"\s+styleIDRef="(\d+)"', paragraph)
    c = re.search(r'charPrIDRef="(\d+)"', paragraph)
    return (m.group(1), m.group(2), c.group(1) if c else "")


def _find(paragraphs, needle):
    return next(p for p in paragraphs if needle in _text(p))


def test_find_anchors_reports_where_and_how_deep():
    hits = find_anchors(FIXTURE, ANCHOR)
    assert len(hits) == 1
    assert hits[0].section == "Contents/section4.xml"
    assert ANCHOR in hits[0].text
    assert hits[0].depth >= 1


def test_insert_adds_paragraphs_and_leaves_the_original_text_alone(tmp_path):
    out = tmp_path / "out.hwpx"
    before = _paragraphs(FIXTURE)
    result = insert_markdown(FIXTURE, "덧붙인 문단", anchor=ANCHOR, output=out)
    after = _paragraphs(out)

    assert result.inserted == 1
    assert len(after) == len(before) + 1
    # every original paragraph survives, in order
    kept = [p for p in after if _text(p) != "덧붙인 문단"]
    assert [_text(p) for p in kept] == [_text(p) for p in before]


def test_new_paragraph_borrows_the_anchor_style(tmp_path):
    out = tmp_path / "out.hwpx"
    insert_markdown(FIXTURE, "덧붙인 문단", anchor=ANCHOR, output=out)
    after = _paragraphs(out)
    assert _ids(_find(after, "덧붙인 문단")) == _ids(_find(after, ANCHOR))


def test_bullet_levels_walk_the_local_indent_ladder(tmp_path):
    """A level-1 bullet sits where the anchor sits; level 2 one indent in."""
    out = tmp_path / "out.hwpx"
    md = "* 같은 수준\n  * 한 칸 안쪽"
    insert_markdown(FIXTURE, md, anchor=ANCHOR, output=out)
    after = _paragraphs(out)
    assert _ids(_find(after, "같은 수준")) == _ids(_find(after, ANCHOR))
    assert _ids(_find(after, "한 칸 안쪽")) != _ids(_find(after, ANCHOR))


def test_before_and_after_place_on_the_right_side(tmp_path):
    for where, offset in (("after", 1), ("before", -1)):
        out = tmp_path / f"{where}.hwpx"
        insert_markdown(FIXTURE, "표지", anchor=ANCHOR, where=where, output=out)
        after = _paragraphs(out)
        i = next(n for n, p in enumerate(after) if "표지" == _text(p))
        assert ANCHOR in _text(after[i - offset])


def test_existing_line_layout_cache_is_preserved(tmp_path):
    """Stripping it would demote every outline heading in the section to body text."""
    out = tmp_path / "out.hwpx"
    before = _paragraphs(FIXTURE)
    insert_markdown(FIXTURE, "덧붙인 문단", anchor=ANCHOR, output=out)
    after = _paragraphs(out)
    n_before = sum("linesegarray" in p for p in before)
    n_after = sum("linesegarray" in p for p in after)
    assert n_after == n_before  # untouched paragraphs keep theirs, the new one has none
    assert "linesegarray" not in _find(after, "덧붙인 문단")


def test_container_is_preserved_byte_for_byte_apart_from_the_edited_section(tmp_path):
    out = tmp_path / "out.hwpx"
    insert_markdown(FIXTURE, "덧붙인 문단", anchor=ANCHOR, output=out)
    with zipfile.ZipFile(FIXTURE) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        changed = [n.filename for n in a.infolist() if a.read(n.filename) != b.read(n.filename)]
        assert changed == ["Contents/section4.xml"]
        first = b.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED


def test_ambiguous_anchor_is_an_error_not_a_guess(tmp_path):
    hits = find_anchors(FIXTURE, "서귀포")
    assert len(hits) > 1  # the fixture mentions it many times
    with pytest.raises(TextEditError, match="일치"):
        insert_markdown(FIXTURE, "x", anchor="서귀포", output=tmp_path / "o.hwpx")


def test_occurrence_picks_one_of_several(tmp_path):
    out = tmp_path / "out.hwpx"
    hits = find_anchors(FIXTURE, "서귀포")
    result = insert_markdown(
        FIXTURE, "고른 자리", anchor="서귀포", occurrence=2, output=out
    )
    assert result.anchor.index == hits[1].index


def test_missing_anchor_is_an_error(tmp_path):
    with pytest.raises(TextEditError, match="찾지 못함"):
        insert_markdown(FIXTURE, "x", anchor="이런 문장은 없다", output=tmp_path / "o.hwpx")


def test_headings_and_tables_are_refused(tmp_path):
    for md in ("# 새 장 제목", "| a | b |\n|---|---|\n| 1 | 2 |"):
        with pytest.raises(TextEditError, match="범위 밖"):
            insert_markdown(FIXTURE, md, anchor=ANCHOR, output=tmp_path / "o.hwpx")


def test_empty_content_is_an_error(tmp_path):
    with pytest.raises(TextEditError, match="삽입할 내용"):
        insert_markdown(FIXTURE, "   \n\n", anchor=ANCHOR, output=tmp_path / "o.hwpx")


def test_special_characters_are_escaped(tmp_path):
    out = tmp_path / "out.hwpx"
    insert_markdown(FIXTURE, "A < B & C > D", anchor=ANCHOR, output=out)
    with zipfile.ZipFile(out) as zf:
        xml = zf.read("Contents/section4.xml").decode("utf-8")
    assert "<hp:t>A &lt; B &amp; C &gt; D</hp:t>" in xml


def test_anchor_level_pins_the_markdown_outline_onto_the_document(tmp_path):
    """Anchoring on a second-level bullet must not flatten a top-level one onto it."""
    deep = "글로벌 경기 둔화"  # a paragraph one indent in from its heading
    md = "* 위 수준\n  * 아래 수준"

    flat = tmp_path / "flat.hwpx"
    insert_markdown(FIXTURE, md, anchor=deep, output=flat)
    after = _paragraphs(flat)
    # with the default the top-level bullet lands where the anchor sits
    assert _ids(_find(after, "위 수준")) == _ids(_find(after, deep))

    stepped = tmp_path / "stepped.hwpx"
    insert_markdown(FIXTURE, md, anchor=deep, output=stepped, anchor_level=2)
    after = _paragraphs(stepped)
    # declaring the anchor a level-2 bullet steps the top-level one an indent out
    assert _ids(_find(after, "위 수준")) != _ids(_find(after, deep))
    assert _ids(_find(after, "아래 수준")) == _ids(_find(after, deep))


def test_anchor_level_must_be_positive(tmp_path):
    with pytest.raises(TextEditError, match="anchor_level"):
        insert_markdown(
            FIXTURE, "x", anchor=ANCHOR, output=tmp_path / "o.hwpx", anchor_level=0
        )


# --------------------------------------------------------------------------- #
# synthetic packages — shapes the real fixture doesn't have
# --------------------------------------------------------------------------- #
_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"'
    ' xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">'
    '<hh:refList><hh:paraProperties itemCnt="2">'
    '<hh:paraPr id="0"><hh:heading type="NONE"/>'
    '<hh:margin><hc:left value="0"/><hc:intent value="0"/></hh:margin></hh:paraPr>'
    '<hh:paraPr id="1"><hh:heading type="BULLET"/>'
    '<hh:margin><hc:left value="1000"/><hc:intent value="0"/></hh:margin></hh:paraPr>'
    "</hh:paraProperties></hh:refList></hh:head>"
)


def _para(text: str, *, para_pr: str = "0", lead: str = "") -> str:
    return (
        f'<hp:p id="7" paraPrIDRef="{para_pr}" styleIDRef="0">'
        f'{lead}<hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'
    )


def _package(tmp_path: Path, sections: dict[str, str], name: str = "doc.hwpx") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/header.xml", _HEADER)
        for part, body in sections.items():
            zf.writestr(part, f'<?xml version="1.0"?><hs:sec xmlns:hp="x">{body}</hs:sec>')
    return path


def test_sections_are_read_in_document_order_past_ten(tmp_path):
    """Lexically section10 precedes section2 — --occurrence would target the wrong one."""
    src = _package(
        tmp_path,
        {f"Contents/section{n}.xml": _para(f"공통 문구 {n}") for n in range(12)},
    )
    hits = find_anchors(src, "공통 문구")
    assert [h.section for h in hits] == [f"Contents/section{n}.xml" for n in range(12)]


def test_occurrence_out_of_range_is_an_error_not_a_traceback(tmp_path):
    src = _package(tmp_path, {"Contents/section0.xml": _para("한 번만 나오는 말")})
    with pytest.raises(TextEditError, match="1..1"):
        insert_markdown(
            src, "새 문단", anchor="한 번만", occurrence=5, output=tmp_path / "o.hwpx"
        )


def test_anchor_matches_what_hangul_displays_not_the_escaped_source(tmp_path):
    src = _package(tmp_path, {"Contents/section0.xml": _para("연구 &amp; 개발 부문")})
    hits = find_anchors(src, "연구 & 개발")
    assert len(hits) == 1
    assert hits[0].text == "연구 & 개발 부문"


def test_clone_skips_a_textless_control_run(tmp_path):
    """A section's first paragraph carries <hp:secPr> in a leading textless run."""
    lead = '<hp:run charPrIDRef="0"><hp:secPr id="9"/></hp:run>'
    src = _package(tmp_path, {"Contents/section0.xml": _para("구역 첫 문단", lead=lead)})
    out = tmp_path / "out.hwpx"
    insert_markdown(src, "새 문단", anchor="구역 첫 문단", output=out)
    added = _find(_paragraphs(out, "Contents/section0.xml"), "새 문단")
    assert _text(added) == "새 문단"
    assert "secPr" not in added


def test_manual_bullet_head_is_carried_onto_the_new_item(tmp_path):
    """JI 관행: the marker is literal text, so cloning properties alone loses it."""
    src = _package(tmp_path, {"Contents/section0.xml": _para("￭ 기존 항목")})
    out = tmp_path / "out.hwpx"
    result = insert_markdown(src, "* 새 항목", anchor="기존 항목", output=out)
    texts = [_text(p) for p in _paragraphs(out, "Contents/section0.xml")]
    assert "￭ 새 항목" in texts
    assert not result.warnings


def test_bullet_onto_an_unmarked_template_warns(tmp_path):
    src = _package(tmp_path, {"Contents/section0.xml": _para("그냥 본문 문단")})
    out = tmp_path / "out.hwpx"
    result = insert_markdown(src, "* 새 항목", anchor="그냥 본문", output=out)
    assert any("글머리 표시가 없어" in w for w in result.warnings)
