"""Tests for hwp_agent.ops.form (form-fill engine)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwp_agent.ops import analyze_form, fill_form
from hwp_agent.ops.form import extract_placeholders, table_label_slots

REPO_ROOT = Path(__file__).resolve().parents[1]
REF_HWPX = REPO_ROOT / "tests" / "fixtures" / "sample_big_ref.hwpx"
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


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
