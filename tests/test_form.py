"""Tests for hwp_agent.ops.form (form-fill engine)."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.ops import analyze_form, dump_grid, fill_form
from hwp_agent.ops.form import (
    _heal_split_placeholders,
    _is_label_shaped,
    _resolve_tab_anchor,
    _set_cell_text_overwrite,
    _set_tab_tail,
    _toggle_checkbox,
    extract_placeholders,
    q,
    table_label_slots,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REF_HWPX = REPO_ROOT / "tests" / "fixtures" / "sample_big_ref.hwpx"
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _root(inner: str):
    """Parse a section-ish XML fragment into an lxml root (like a live tree)."""
    return etree.fromstring(
        f'<hs:sec xmlns:hs="x" xmlns:hp="{_HP}">{inner}</hs:sec>'.encode()
    )


def test_extract_placeholders_unique_in_order() -> None:
    text = "과제명 {{ title }} / 책임자 {{author}} / 또 {{title}}"
    assert extract_placeholders(text) == ["title", "author"]


def test_extract_placeholders_none() -> None:
    assert extract_placeholders("플레이스홀더 없음") == []


def _cell(col: int, row: int, text: str) -> str:
    return (
        f'<hp:tc><hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        f"<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>"
        f"</hp:subList></hp:tc>"
    )


def test_table_label_slots_detects_blank_neighbours() -> None:
    section = (
        f'<hs:sec xmlns:hs="x" xmlns:hp="{_HP}"><hp:tbl>'
        f'<hp:tr>{_cell(0, 0, "과제명")}{_cell(1, 0, "")}</hp:tr>'
        f'<hp:tr>{_cell(0, 1, "연구책임자")}{_cell(1, 1, "")}</hp:tr>'
        f"</hp:tbl></hs:sec>"
    ).encode()

    slots = table_label_slots(section)
    assert {s.name for s in slots} == {"과제명", "연구책임자"}
    assert all(s.kind == "cell" and s.locator.endswith("> right") for s in slots)


def test_table_label_slots_skips_filled_neighbours() -> None:
    section = (
        f'<hs:sec xmlns:hs="x" xmlns:hp="{_HP}"><hp:tbl>'
        f'<hp:tr>{_cell(0, 0, "과제명")}{_cell(1, 0, "이미 채워짐")}</hp:tr>'
        f"</hp:tbl></hs:sec>"
    ).encode()
    assert table_label_slots(section) == []


# --- hardening: overwrite / checkbox / tab-tail / split-placeholder ---------


def test_is_label_shaped() -> None:
    assert _is_label_shaped("성명")
    assert not _is_label_shaped("이것은 라벨이 아니라 완성된 문장입니다.")
    assert not _is_label_shaped("2026. 6. 4.")


def test_overwrite_replaces_nonempty_cell() -> None:
    tc = _root(
        '<hp:tc><hp:subList><hp:p><hp:run><hp:t>이전값</hp:t></hp:run>'
        "<hp:run><hp:t>잔여</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    ).find(q("tc"))
    assert _set_cell_text_overwrite(tc, "새값")
    # SET, not append: the cell now holds exactly "새값".
    assert "".join(t.text or "" for t in tc.iter(q("t"))) == "새값"


def test_overwrite_fills_empty_run() -> None:
    tc = _root(
        '<hp:tc><hp:subList><hp:p><hp:run charPrIDRef="3"/></hp:p>'
        "</hp:subList></hp:tc>"
    ).find(q("tc"))
    assert _set_cell_text_overwrite(tc, "값")
    assert "".join(t.text or "" for t in tc.iter(q("t"))) == "값"


def test_overwrite_multiline_makes_real_paragraphs() -> None:
    tc = _root(
        '<hp:tc><hp:subList><hp:p paraPrIDRef="7"><hp:run charPrIDRef="3">'
        "<hp:t>이전값</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    ).find(q("tc"))
    assert _set_cell_text_overwrite(tc, "첫째 줄\n둘째 줄\n셋째 줄")
    ps = tc.find(q("subList")).findall(q("p"))
    # one <hp:p> per line — ENTER paragraphs, not SHIFT+ENTER soft breaks
    assert len(ps) == 3
    texts = ["".join(t.text or "" for t in p.iter(q("t"))) for p in ps]
    assert texts == ["첫째 줄", "둘째 줄", "셋째 줄"]
    # no literal newline survives inside any text node
    assert not any("\n" in (t.text or "") for t in tc.iter(q("t")))
    # clones keep the first paragraph's para/char style refs
    assert {p.get("paraPrIDRef") for p in ps} == {"7"}
    assert all(r.get("charPrIDRef") == "3" for p in ps for r in p.findall(q("run")))


def test_overwrite_multiline_from_empty_run() -> None:
    tc = _root(
        '<hp:tc><hp:subList><hp:p><hp:run charPrIDRef="3"/></hp:p>'
        "</hp:subList></hp:tc>"
    ).find(q("tc"))
    assert _set_cell_text_overwrite(tc, "가\n나")
    ps = tc.find(q("subList")).findall(q("p"))
    assert ["".join(t.text or "" for t in p.iter(q("t"))) for p in ps] == ["가", "나"]


def test_toggle_checkbox_glyph_then_label() -> None:
    root = _root("<hp:p><hp:run><hp:t>□ 동의함</hp:t></hp:run></hp:p>")
    assert _toggle_checkbox([root], "동의함", on=True)
    assert "■ 동의함" in next(root.iter(q("t"))).text
    # idempotent
    assert not _toggle_checkbox([root], "동의함", on=True)


def test_toggle_checkbox_label_then_glyph() -> None:
    root = _root("<hp:p><hp:run><hp:t>수집·이용에 동의합니다 □</hp:t></hp:run></hp:p>")
    assert _toggle_checkbox([root], "동의합니다", on=True)
    assert "동의합니다 ■" in next(root.iter(q("t"))).text


def test_tab_tail_field() -> None:
    root = _root("<hp:p><hp:run><hp:t>소속<hp:tab/>이전</hp:t></hp:run></hp:p>")
    t = _resolve_tab_anchor([root], "소속")
    assert t is not None
    assert _set_tab_tail(t, "제주연구원")
    assert t.find(q("tab")).tail == "제주연구원"


def test_heal_split_placeholder() -> None:
    root = _root(
        "<hp:p><hp:run><hp:t>{{na</hp:t></hp:run>"
        "<hp:run><hp:t>me}}</hp:t></hp:run></hp:p>"
    )
    assert _heal_split_placeholders([root]) == 1
    joined = "".join(t.text or "" for t in root.iter(q("t")))
    assert "{{name}}" in joined


# --- end-to-end on the real reference document ---------------------------


@pytest.mark.skipif(not REF_HWPX.is_file(), reason="reference HWPX not present")
def test_analyze_real_form_returns_slots() -> None:
    spec = analyze_form(REF_HWPX)
    # The reference has tables, so label-cell candidates should be present;
    # at minimum analyze runs cleanly and returns a spec.
    assert isinstance(spec.names(), list)


@pytest.mark.skipif(not REF_HWPX.is_file(), reason="reference HWPX not present")
def test_fill_by_explicit_cell_path(tmp_path: Path) -> None:
    out = tmp_path / "filled.hwpx"
    result = fill_form(
        REF_HWPX,
        {"연구요약 > right": "AI가 채운 값 『테스트』"},
        output=out,
    )
    assert result.filled == ["연구요약 > right"]
    assert not result.missing

    from hwpx.document import HwpxDocument

    doc = HwpxDocument.open(str(out))
    target = doc.find_cell_by_label("연구요약")["matches"][0]["target_cell"]["text"]
    assert "AI가 채운 값" in target


@pytest.mark.skipif(not REF_HWPX.is_file(), reason="reference HWPX not present")
def test_fill_reports_missing_for_unknown(tmp_path: Path) -> None:
    out = tmp_path / "f.hwpx"
    result = fill_form(REF_HWPX, {"존재하지않는라벨": "x"}, output=out)
    assert result.missing == ["존재하지않는라벨"]
    assert not result.filled


@pytest.mark.skipif(not REF_HWPX.is_file(), reason="reference HWPX not present")
def test_dump_grid_addresses_prefilled_cells() -> None:
    """`analyze_form` skips non-empty cells; `dump_grid` still addresses them."""
    cells = dump_grid(REF_HWPX)
    assert cells, "reference form has tables"

    # every cell carries the cell:<table>:<row>:<col> key that fill_form accepts
    for c in cells:
        assert c.cell_path == f"cell:{c.table_index}:{c.row}:{c.col}"

    # the point of the command: cells that already hold text are listed too,
    # even though analyze_form never reports them as slots
    filled = [c for c in cells if c.text]
    assert filled, "reference form has pre-filled cells"
    slot_paths = {s.cell_path for s in analyze_form(REF_HWPX).slots}
    assert any(c.cell_path not in slot_paths for c in filled)


@pytest.mark.skipif(not REF_HWPX.is_file(), reason="reference HWPX not present")
def test_dump_grid_paths_are_fillable(tmp_path: Path) -> None:
    """A path from dump_grid round-trips through fill_form."""
    target = next(c for c in dump_grid(REF_HWPX) if c.text)
    out = tmp_path / "filled.hwpx"
    fill_form(REF_HWPX, {target.cell_path: "덮어쓴 값"}, output=out)
    after = {c.cell_path: c.text for c in dump_grid(out)}
    assert after[target.cell_path] == "덮어쓴 값"


def test_release_table_flow_toggles_attrs() -> None:
    """--allow-table-flow releases treatAsChar/pageBreak so content can flow (#12)."""
    from lxml import etree

    from hwp_agent.ops.form import _release_table_flow, _trapped_table_count, q

    hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    root = etree.fromstring(
        f'<hs:sec xmlns:hs="urn:x" xmlns:hp="{hp}">'
        '<hp:tbl pageBreak="TABLE"><hp:pos treatAsChar="1"/></hp:tbl>'
        '<hp:tbl pageBreak="CELL"><hp:pos treatAsChar="0"/></hp:tbl>'
        "</hs:sec>"
    )
    assert _trapped_table_count([root]) == 1
    assert _release_table_flow([root]) == 1
    trapped = root.findall(q("tbl"))[0]
    assert trapped.get("pageBreak") == "CELL"
    assert trapped.find(q("pos")).get("treatAsChar") == "0"
    assert _trapped_table_count([root]) == 0  # nothing trapped anymore


_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "정책과제-template.hwpx"


@pytest.mark.skipif(not _EXAMPLE.is_file(), reason="example template not present")
def test_autofit_preserves_total(tmp_path: Path) -> None:
    """autofit redistributes column widths but keeps the total (#13)."""
    from hwp_agent.ops import autofit_table

    out = tmp_path / "af.hwpx"
    s = autofit_table(_EXAMPLE, 0, output=out)
    assert out.is_file() and s["ncols"] >= 2
    assert sum(s["new_widths"]) == s["total"] == sum(s["old_widths"])
    assert all(w > 0 for w in s["new_widths"])


@pytest.mark.skipif(not _EXAMPLE.is_file(), reason="example template not present")
def test_autofit_min_width_pins_column(tmp_path: Path) -> None:
    from hwp_agent.ops import autofit_table

    s = autofit_table(_EXAMPLE, 0, min_widths={0: 20000}, output=tmp_path / "af2.hwpx")
    assert s["new_widths"][0] >= 20000  # the pinned floor is honoured
    assert sum(s["new_widths"]) == s["total"]


def _make_label_slots_fixture(path: Path) -> Path:
    from hwpx.document import HwpxDocument

    doc = HwpxDocument.new()
    doc.add_table(1, 1)

    sec_obj = doc.sections[0]
    sec = sec_obj.element
    ps = sec.findall(q("p"))
    p_tbl = ps[1]
    tbl_idx = list(sec).index(p_tbl)

    def _make_p(text: str, is_empty: bool = False):
        p = etree.Element(q("p"), {"paraPrIDRef": "0", "styleIDRef": "0"})
        run = etree.SubElement(p, q("run"), {"charPrIDRef": "0"})
        if not is_empty:
            t = etree.SubElement(run, q("t"))
            t.text = text
        return p

    before_labels = [
        _make_p("□ 과제유형 : "),
        _make_p("□ 과 제 명 : "),
        _make_p("□ 과제책임 : "),
        _make_p("□ 과제기간 :     .   .   . ~     .   .   ."),
        _make_p("□ 변경사항 :"),
    ]
    for offset, p in enumerate(before_labels):
        sec.insert(tbl_idx + offset, p)

    after_labels = [
        _make_p("□ 변경사유 :"),
        _make_p("", is_empty=True),
        _make_p("□ 동의함"),
    ]
    for p in after_labels:
        sec.append(p)

    sec_obj.mark_dirty()
    doc.save_to_path(str(path))
    return path


def test_label_and_section_slots_analysis_and_fill(tmp_path: Path) -> None:
    """Issue #66: label: and section: discovery, checkbox false positives fixed."""
    fixture_path = _make_label_slots_fixture(tmp_path / "label_form.hwpx")
    spec = analyze_form(fixture_path)

    by_kind: dict[str, list] = {}
    for s in spec.slots:
        by_kind.setdefault(s.kind, []).append(s)

    labels = {s.locator: s for s in by_kind.get("label", [])}
    assert "label:과제유형" in labels
    assert "label:과 제 명" in labels  # preserves raw label with whitespace
    assert "label:과제책임" in labels
    assert "label:과제기간" in labels
    assert labels["label:과제기간"].current == ".   .   . ~     .   .   ."

    sections = {s.locator: s for s in by_kind.get("section", [])}
    assert "section:변경사유" in sections
    assert "section:변경사항" not in sections  # table header is not a section slot
    assert "label:변경사항" not in labels

    # Checkbox false positives: label lines must not be classified as checkboxes
    checkboxes = {s.locator: s for s in by_kind.get("checkbox", [])}
    assert "checkbox:동의함" in checkboxes
    assert "checkbox:과제유형" not in checkboxes
    assert "checkbox:과" not in checkboxes
    assert "checkbox:과 제 명" not in checkboxes
    assert "checkbox:변경사항" not in checkboxes
    assert "checkbox:변경사유" not in checkboxes

    # Fill test: support whitespace-insensitive matching (label:과제명 -> 과 제 명)
    out_path = tmp_path / "filled.hwpx"
    res = fill_form(
        fixture_path,
        {
            "label:과제유형": "일반",
            "label:과제명": "제주 생태 연구",
            "label:과제책임": "홍길동",
            "label:과제기간": "2026. 3. 1. ~ 2026. 12. 31.",
            "section:변경사유": "- 사유 1: 환경 변화\n- 사유 2: 예산 조정",
            "checkbox:동의함": "on",
        },
        output=out_path,
    )
    assert not res.missing
    assert len(res.filled) == 6

    # Verify filled content
    from hwpx.document import HwpxDocument

    filled_doc = HwpxDocument.open(str(out_path))
    sec = filled_doc.sections[0].element
    ps_text = ["".join(p.itertext()) for p in sec.findall(q("p"))]

    assert "□ 과제유형 : 일반" in ps_text
    assert "□ 과 제 명 : 제주 생태 연구" in ps_text
    assert "□ 과제책임 : 홍길동" in ps_text
    assert "□ 과제기간 : 2026. 3. 1. ~ 2026. 12. 31." in ps_text
    assert "□ 변경사항 :" in ps_text
    assert "□ 변경사유 :" in ps_text
    assert "- 사유 1: 환경 변화" in ps_text
    assert "- 사유 2: 예산 조정" in ps_text
    assert "■ 동의함" in ps_text

    # Re-fill (overwrite) test: ensure previously filled labels can be updated (#68 review finding)
    out_path2 = tmp_path / "refilled.hwpx"
    res2 = fill_form(
        out_path,
        {
            "label:과제유형": "특별",
            "label:과제명": "수정된 연구",
        },
        output=out_path2,
    )
    assert not res2.missing
    assert len(res2.filled) == 2
    filled_doc2 = HwpxDocument.open(str(out_path2))
    ps_text2 = ["".join(p.itertext()) for p in filled_doc2.sections[0].element.findall(q("p"))]
    assert "□ 과제유형 : 특별" in ps_text2
    assert "□ 과 제 명 : 수정된 연구" in ps_text2


def test_label_slot_clears_tab_and_trailing_content(tmp_path: Path) -> None:
    """Issue #68 review finding: trailing inline objects (<hp:tab/> etc.) must be cleared."""
    from hwpx.document import HwpxDocument

    doc = HwpxDocument.new()
    sec = doc.sections[0].element
    p = etree.SubElement(sec, q("p"), {"paraPrIDRef": "0", "styleIDRef": "0"})
    run = etree.SubElement(p, q("run"), {"charPrIDRef": "0"})
    t = etree.SubElement(run, q("t"))
    t.text = "□ 기간 :"
    tab = etree.SubElement(t, q("tab"))
    tab.tail = " . . . ~ . . ."
    extra_run = etree.SubElement(p, q("run"), {"charPrIDRef": "0"})
    extra_t = etree.SubElement(extra_run, q("t"))
    extra_t.text = " (추가안내)"

    doc.sections[0].mark_dirty()
    fixture = tmp_path / "tab_label.hwpx"
    doc.save_to_path(str(fixture))

    out = tmp_path / "tab_label_out.hwpx"
    res = fill_form(fixture, {"label:기간": "2026. 10. 26."}, output=out)
    assert not res.missing
    filled = HwpxDocument.open(str(out))
    filled_p = filled.sections[0].element.findall(q("p"))[-1]
    assert "".join(filled_p.itertext()) == "□ 기간 : 2026. 10. 26."
    assert filled_p.find(f".//{q('tab')}") is None


def test_section_slot_preserves_picture_paragraph(tmp_path: Path) -> None:
    """Issue #68 review finding: non-text objects like pic must not be treated as empty."""
    from hwpx.document import HwpxDocument

    doc = HwpxDocument.new()
    sec = doc.sections[0].element
    p_label = etree.SubElement(sec, q("p"), {"paraPrIDRef": "0", "styleIDRef": "0"})
    r_label = etree.SubElement(p_label, q("run"), {"charPrIDRef": "0"})
    t_label = etree.SubElement(r_label, q("t"))
    t_label.text = "□ 변경사유 :"

    p_empty = etree.SubElement(sec, q("p"), {"paraPrIDRef": "0", "styleIDRef": "0"})
    etree.SubElement(p_empty, q("run"), {"charPrIDRef": "0"})

    # Paragraph with a picture object (no text)
    p_pic = etree.SubElement(sec, q("p"), {"paraPrIDRef": "0", "styleIDRef": "0"})
    r_pic = etree.SubElement(p_pic, q("run"), {"charPrIDRef": "0"})
    etree.SubElement(r_pic, q("pic"), {"id": "123"})

    doc.sections[0].mark_dirty()
    fixture = tmp_path / "sec_pic.hwpx"
    doc.save_to_path(str(fixture))

    out = tmp_path / "sec_pic_out.hwpx"
    res = fill_form(fixture, {"section:변경사유": "서술 내용"}, output=out)
    assert not res.missing

    filled = HwpxDocument.open(str(out))
    ps = filled.sections[0].element.findall(q("p"))
    # The picture paragraph must still exist!
    assert any(p.find(f".//{q('pic')}") is not None for p in ps)
    assert any("서술 내용" in "".join(p.itertext()) for p in ps)
