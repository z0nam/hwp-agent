"""Tests for hwp_agent.ops.form (form-fill engine)."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.ops import analyze_form, fill_form
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
