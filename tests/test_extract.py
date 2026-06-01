"""Tests for hwp_agent.ops.extract (HWPX → body-focused Markdown)."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from hwp_agent.ops import extract_markdown
from hwp_agent.ops.extract import _emit_table

REPO_ROOT = Path(__file__).resolve().parents[1]
APPENDIX = REPO_ROOT / "tests" / "fixtures" / "sample_appendix.hwpx"
TYPE1 = REPO_ROOT / "tests" / "fixtures" / "sample_hwpx.hwpx"

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


# --------------------------------------------------------------------------- #
# table emission — unmerge logic exercised with synthetic XML
# --------------------------------------------------------------------------- #
def _mk_table(rows: int, cols: int, cells: list[tuple[int, int, int, int, str]]):
    """Synthetic <hp:tbl> for testing. cells = (row, col, rowSpan, colSpan, text)."""
    trs = ""
    by_row: dict[int, list[tuple[int, int, int, str]]] = {}
    for r, c, rs, cs, text in cells:
        by_row.setdefault(r, []).append((c, rs, cs, text))
    for r in range(rows):
        tcs = ""
        for c, rs, cs, text in sorted(by_row.get(r, [])):
            tcs += (
                f'<hp:tc header="0">'
                f'<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
                f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/>'
                f'<hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/>'
                f"</hp:tc>"
            )
        trs += f"<hp:tr>{tcs}</hp:tr>"
    xml = (
        f'<hp:tbl xmlns:hp="{_HP}" rowCnt="{rows}" colCnt="{cols}"'
        f' borderFillIDRef="5">{trs}</hp:tbl>'
    )
    return ET.fromstring(xml)


def test_simple_rectangular_table_becomes_pipe_table() -> None:
    """A plain rectangular table → MD pipe table with the first row as header."""
    tbl = _mk_table(
        2, 3,
        [
            (0, 0, 1, 1, "h1"), (0, 1, 1, 1, "h2"), (0, 2, 1, 1, "h3"),
            (1, 0, 1, 1, "a"), (1, 1, 1, 1, "b"), (1, 2, 1, 1, "c"),
        ],
    )
    lines: list[str] = []
    _emit_table(lines, tbl)
    assert "| h1 | h2 | h3 |" in lines
    assert "|---|---|---|" in lines
    assert "| a | b | c |" in lines


def test_merged_cells_are_unmerged_by_duplicating_value() -> None:
    """A colSpan=2 cell becomes two adjacent cells with the same text (Excel-style)."""
    # 2x3: header row has "구분" spanning cols 0-1, then "내용" in col 2.
    tbl = _mk_table(
        2, 3,
        [
            (0, 0, 1, 2, "구분"), (0, 2, 1, 1, "내용"),
            (1, 0, 1, 1, "범위"), (1, 1, 1, 1, "분석대상"), (1, 2, 1, 1, "서귀포"),
        ],
    )
    lines: list[str] = []
    _emit_table(lines, tbl)
    # 구분 is duplicated into both columns 0 and 1
    assert "| 구분 | 구분 | 내용 |" in lines
    assert "| 범위 | 분석대상 | 서귀포 |" in lines


def test_rowspan_cell_value_is_duplicated_down() -> None:
    """rowSpan=2 → value appears in both rows of that column."""
    tbl = _mk_table(
        2, 2,
        [
            (0, 0, 2, 1, "A형"),  # row 0+1, col 0
            (0, 1, 1, 1, "A"),
            (1, 1, 1, 1, "B"),
        ],
    )
    lines: list[str] = []
    _emit_table(lines, tbl)
    assert "| A형 | A |" in lines
    assert "| A형 | B |" in lines  # rowspan duplicated


def test_table_caption_emitted_above() -> None:
    """An <hp:caption> appears as a single line above the table."""
    cap = (
        "<hp:caption><hp:subList><hp:p><hp:run>"
        "<hp:t>표 1 - 사업 일람</hp:t></hp:run></hp:p></hp:subList></hp:caption>"
    )
    cell = (
        "<hp:tr><hp:tc><hp:subList><hp:p><hp:run>"
        "<hp:t>X</hp:t></hp:run></hp:p></hp:subList>"
        '<hp:cellAddr colAddr="0" rowAddr="0"/>'
        '<hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc></hp:tr>'
    )
    xml = (
        f'<hp:tbl xmlns:hp="{_HP}" rowCnt="1" colCnt="1">{cap}{cell}</hp:tbl>'
    )
    tbl = ET.fromstring(xml)
    lines: list[str] = []
    _emit_table(lines, tbl)
    assert lines[0] == "표 1 - 사업 일람"


def test_pipe_in_cell_is_escaped() -> None:
    """A literal `|` inside a cell would break the pipe-table — escape it."""
    tbl = _mk_table(1, 1, [(0, 0, 1, 1, "a|b")])
    lines: list[str] = []
    _emit_table(lines, tbl)
    assert any(r"a\|b" in line for line in lines)


# --------------------------------------------------------------------------- #
# integration — real fixtures
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_extract_produces_markdown(tmp_path: Path) -> None:
    """A round-trip of the simple sample produces non-empty Markdown."""
    md = extract_markdown(TYPE1)
    assert md and md.endswith("\n")
    # the document has at least some content
    assert len(md.strip()) > 0


@pytest.mark.skipif(not APPENDIX.is_file(), reason="appendix sample not present")
def test_extract_appendix_has_headings_and_tables() -> None:
    """Headings (`#`/`##`/...), bullets (`*`), and a pipe table all show up."""
    md = extract_markdown(APPENDIX)
    lines = md.splitlines()
    assert any(line.startswith("# ") for line in lines)
    assert any(line.startswith("* ") for line in lines)
    assert any(line.startswith("|---") for line in lines)  # at least one MD table


def test_extract_cli_writes_file(tmp_path: Path, capsys) -> None:
    """`hwp-agent extract … -o OUT.md` writes the Markdown to disk."""
    from hwp_agent.cli.main import main

    if not TYPE1.is_file():
        pytest.skip("type-1 sample not present")
    out = tmp_path / "out.md"
    rc = main(["extract", str(TYPE1), "-o", str(out)])
    assert rc == 0
    assert out.is_file() and out.read_text(encoding="utf-8").strip()
    err = capsys.readouterr().out
    assert "wrote" in err
