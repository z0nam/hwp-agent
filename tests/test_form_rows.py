"""Tests for form-fill row restructuring (issue #43): delete / append / repeat.

The fixture table mirrors an institutional "sample rows" form, merges included —
covered cells are *absent* from the XML, as Hangul saves them:

    row 0  [구분 (rows 0-1)] [내용]   [비고]       ← header, vertical merge
    row 1                   [세부]   [값]
    row 2  [Ⅰ. 대분류]       [1행]   [a]          ← group header (shaded)
    row 3  [소 (rows 3-4)]   [1.]    [x]          ← vertical merge inside a group
    row 4                   [2.]    [y]
    row 5  [Ⅱ. 대분류]       [2행]   [b]
    row 6  [끝]              [마지막] [z]          ← last row
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from hwpx.document import HwpxDocument
from lxml import etree

from hwp_agent.ops import RowOp, RowOpError, dump_grid, fill_form, parse_row_op
from hwp_agent.ops.form import _set_cell_text_overwrite, q
from hwp_agent.ops.integrity import audit_package
from hwp_agent.ops.linesegs import iter_mismatches

ROWS = [
    ["구분", "내용", "비고"],
    [None, "세부", "값"],
    ["Ⅰ. 대분류", "1행", "a"],
    ["소", "1.", "x"],
    [None, "2.", "y"],
    ["Ⅱ. 대분류", "2행", "b"],
    ["끝", "마지막", "z"],
]
MERGES = [(0, 0, 2), (3, 0, 2)]  # (row, col, rowSpan)
HEIGHTS = [1000, 1100, 1200, 1300, 1400, 1500, 1600]


def _make_form(path: Path) -> Path:
    doc = HwpxDocument.new()
    tbl = doc.add_table(len(ROWS), 3).element
    tbl.find(q("sz")).set("height", str(sum(HEIGHTS)))
    trs = tbl.findall(q("tr"))
    for r, tr in enumerate(trs):
        for c, tc in enumerate(tr.findall(q("tc"))):
            # distinct format refs per row so cloning is observable
            tc.set("borderFillIDRef", "2" if r in (2, 5) else "3")
            tc.find(q("cellSz")).set("height", str(HEIGHTS[r]))
            if ROWS[r][c] is not None:
                _set_cell_text_overwrite(tc, ROWS[r][c])
    for r, c, span in MERGES:
        tc = trs[r].findall(q("tc"))[c]
        tc.find(q("cellSpan")).set("rowSpan", str(span))
        tc.find(q("cellSz")).set("height", str(sum(HEIGHTS[r : r + span])))
        for rr in range(r + 1, r + span):  # Hangul drops covered cells
            trs[rr].remove(trs[rr].findall(q("tc"))[c])
    # a stale layout cache on the last row: clones must not inherit it
    p = trs[6].find(q("tc")).find(q("subList")).find(q("p"))
    lsa = etree.SubElement(p, q("linesegarray"))
    etree.SubElement(lsa, q("lineseg"), {"textpos": "0", "vertpos": "0"})
    doc.save_to_path(str(path))
    return path


@pytest.fixture()
def form(tmp_path: Path) -> Path:
    return _make_form(tmp_path / "form.hwpx")


def _table(path: Path):
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("Contents/section0.xml"))
    return next(root.iter(q("tbl")))


def _cells(tbl) -> list[tuple[int, int, int, str, str]]:
    """(row, col, rowSpan, borderFillIDRef, text) for every cell."""
    out = []
    for tc in tbl.iter(q("tc")):
        a, s = tc.find(q("cellAddr")), tc.find(q("cellSpan"))
        text = "".join(t.text or "" for t in tc.iter(q("t")))
        out.append(
            (int(a.get("rowAddr")), int(a.get("colAddr")), int(s.get("rowSpan")),
             tc.get("borderFillIDRef"), text)
        )
    return out


def _assert_consistent(path: Path) -> None:
    """rowCnt, rowAddr, coverage and table height all agree; package is clean."""
    tbl = _table(path)
    trs = tbl.findall(q("tr"))
    assert int(tbl.get("rowCnt")) == len(trs)
    covered: dict[tuple[int, int], int] = {}
    for i, tr in enumerate(trs):
        for tc in tr.findall(q("tc")):
            a, s = tc.find(q("cellAddr")), tc.find(q("cellSpan"))
            r, c = int(a.get("rowAddr")), int(a.get("colAddr"))
            assert r == i
            for rr in range(r, r + int(s.get("rowSpan"))):
                covered[(rr, c)] = covered.get((rr, c), 0) + 1
    assert covered == {(r, c): 1 for r in range(len(trs)) for c in range(3)}
    row_h = {}
    for tc in tbl.iter(q("tc")):
        if tc.find(q("cellSpan")).get("rowSpan") == "1":
            row_h[int(tc.find(q("cellAddr")).get("rowAddr"))] = int(
                tc.find(q("cellSz")).get("height")
            )
    assert int(tbl.find(q("sz")).get("height")) == sum(row_h.values())
    assert audit_package(path) == []
    assert iter_mismatches(path) == []
    grid_rows = {c.row for c in dump_grid(path) if c.table_index == 0}
    assert grid_rows == set(range(len(trs)))


# --- parsing --------------------------------------------------------------- #
def test_parse_row_specs() -> None:
    assert parse_row_op("append", "cell:0:+3") == RowOp("repeat", 0, None, None, 3, "cell:0:+3")
    assert parse_row_op("delete", "cell:1:2-5") == RowOp("delete", 1, 2, 5, 1, "cell:1:2-5")
    assert parse_row_op("delete", "cell:0:4").start == 4
    rep = parse_row_op("repeat", "cell:0:2-4:+2")
    assert (rep.kind, rep.start, rep.end, rep.count) == ("repeat", 2, 4, 2)


@pytest.mark.parametrize(
    "kind,spec",
    [
        ("append", "cell:0:3"),
        ("append", "cell:0:2-4:+1"),
        ("delete", "cell:0:+3"),
        ("delete", "cell:0:5-2"),
        ("delete", "0:2-4"),
        ("repeat", "cell:0:2-4"),
    ],
)
def test_parse_rejects_bad_specs(kind: str, spec: str) -> None:
    with pytest.raises(RowOpError):
        parse_row_op(kind, spec)


# --- append ---------------------------------------------------------------- #
def test_append_clones_last_row_format_and_blanks_text(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    res = fill_form(form, {}, output=out, row_ops=[parse_row_op("append", "cell:0:+3")])
    assert res.rows == ["table 0: rows 6-6 ×3 → new rows 7-9"]
    _assert_consistent(out)
    cells = _cells(_table(out))
    last = sorted(c for c in cells if c[0] == 6)
    for r in (7, 8, 9):
        new = sorted(c for c in cells if c[0] == r)
        assert [(c[1], c[2], c[3]) for c in new] == [(c[1], c[2], c[3]) for c in last]
        assert all(c[4] == "" for c in new)
    assert any("끝" in c[4] for c in cells if c[0] == 6)
    tbl = _table(out)
    new_tc = tbl.findall(q("tr"))[7].find(q("tc"))
    assert new_tc.find(q("subList")).find(q("p")).find(q("linesegarray")) is None
    assert new_tc.find(q("cellMargin")) is not None


def test_set_addresses_appended_rows(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    res = fill_form(
        form, {"cell:0:8:1": "새 행", "cell:0:6:1": "기존"},
        output=out, row_ops=[parse_row_op("append", "cell:0:+2")],
    )
    assert res.missing == []
    grid = {c.cell_path: c.text for c in dump_grid(out)}
    assert grid["cell:0:8:1"] == "새 행"
    assert grid["cell:0:6:1"] == "기존"


def test_append_refuses_when_last_row_is_mid_merge(tmp_path: Path) -> None:
    form = _make_form(tmp_path / "f.hwpx")
    # drop rows 5-6 → the 3-4 merge now ends on the last row; still fine
    mid = tmp_path / "mid.hwpx"
    fill_form(form, {}, output=mid, row_ops=[parse_row_op("delete", "cell:0:5-6")])
    _assert_consistent(mid)
    # the last row (4) is the bottom half of the 3-4 merge → refuse
    with pytest.raises(RowOpError, match="crosses the range boundary"):
        fill_form(mid, {}, output=tmp_path / "x.hwpx",
                  row_ops=[parse_row_op("append", "cell:0:+1")])


# --- delete ---------------------------------------------------------------- #
def test_delete_with_contained_merge(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    fill_form(form, {}, output=out, row_ops=[parse_row_op("delete", "cell:0:2-5")])
    _assert_consistent(out)
    cells = _cells(_table(out))
    assert sorted({c[0] for c in cells}) == [0, 1, 2]
    assert [c[4] for c in sorted(cells) if c[0] == 2] == ["끝", "마지막", "z"]
    assert int(_table(out).find(q("sz")).get("height")) == sum(HEIGHTS) - sum(HEIGHTS[2:6])


@pytest.mark.parametrize("spec", ["cell:0:1-2", "cell:0:3", "cell:0:4-5"])
def test_delete_refuses_split_merge_and_writes_nothing(
    form: Path, tmp_path: Path, spec: str
) -> None:
    out = tmp_path / "out.hwpx"
    with pytest.raises(RowOpError, match="vertically merged cell"):
        fill_form(form, {"cell:0:2:1": "x"}, output=out,
                  row_ops=[parse_row_op("delete", spec)])
    assert not out.exists()


def test_delete_out_of_range(form: Path, tmp_path: Path) -> None:
    with pytest.raises(RowOpError, match="out of range"):
        fill_form(form, {}, output=tmp_path / "o.hwpx",
                  row_ops=[parse_row_op("delete", "cell:0:6-7")])


def test_multiple_deletes_use_original_numbers(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    ops = [parse_row_op("delete", "cell:0:2"), parse_row_op("delete", "cell:0:5")]
    fill_form(form, {}, output=out, row_ops=ops)
    _assert_consistent(out)
    firsts = [c[4] for c in sorted(_cells(_table(out))) if c[1] == 1]
    assert firsts == ["내용", "세부", "1.", "2.", "마지막"]


def test_overlapping_deletes_refused(form: Path, tmp_path: Path) -> None:
    ops = [parse_row_op("delete", "cell:0:2-4"), parse_row_op("delete", "cell:0:3-5")]
    with pytest.raises(RowOpError, match="overlaps"):
        fill_form(form, {}, output=tmp_path / "o.hwpx", row_ops=ops)


def test_delete_then_append_then_set(form: Path, tmp_path: Path) -> None:
    """Fixed order: delete (original rows) → append (post-delete) → set (final)."""
    out = tmp_path / "out.hwpx"
    ops = [parse_row_op("append", "cell:0:+2"), parse_row_op("delete", "cell:0:2-5")]
    fill_form(form, {"cell:0:4:0": "추가2"}, output=out, row_ops=ops)
    _assert_consistent(out)
    grid = {c.cell_path: c.text for c in dump_grid(out)}
    assert grid["cell:0:2:0"] == "끝"
    assert grid["cell:0:3:0"] == ""
    assert grid["cell:0:4:0"] == "추가2"
    assert "cell:0:5:0" not in grid


# --- repeat-group ---------------------------------------------------------- #
def test_repeat_group_copies_rows_and_contained_merge(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    res = fill_form(form, {}, output=out, row_ops=[parse_row_op("repeat", "cell:0:2-4:+2")])
    assert res.rows == ["table 0: rows 2-4 ×2 → new rows 5-10"]
    _assert_consistent(out)
    cells = _cells(_table(out))
    src = sorted((c[0] - 2, c[1], c[2], c[3]) for c in cells if 2 <= c[0] <= 4)
    for k in (1, 2):
        copy_ = sorted(
            (c[0] - 2 - 3 * k, c[1], c[2], c[3]) for c in cells if 2 + 3 * k <= c[0] <= 4 + 3 * k
        )
        assert copy_ == src  # same shape: shaded header + the 2-row merge
        assert all(c[4] == "" for c in cells if 2 + 3 * k <= c[0] <= 4 + 3 * k)
    # the rows after the group moved down by 6, text intact
    assert [c[4] for c in sorted(cells) if c[0] == 11] == ["Ⅱ. 대분류", "2행", "b"]
    assert [c[4] for c in sorted(cells) if c[0] == 12] == ["끝", "마지막", "z"]


def test_repeat_single_row_inserts_after_it(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    fill_form(form, {"cell:0:3:1": "새 소분류"}, output=out,
              row_ops=[parse_row_op("repeat", "cell:0:2:+1")])
    _assert_consistent(out)
    grid = {c.cell_path: c.text for c in dump_grid(out)}
    assert grid["cell:0:2:0"] == "Ⅰ. 대분류"
    assert grid["cell:0:3:1"] == "새 소분류"
    assert grid["cell:0:4:0"] == "소"


@pytest.mark.parametrize("spec", ["cell:0:2-3:+1", "cell:0:4-5:+1", "cell:0:0:+1"])
def test_repeat_refuses_split_merge(form: Path, tmp_path: Path, spec: str) -> None:
    with pytest.raises(RowOpError, match="vertically merged cell"):
        fill_form(form, {}, output=tmp_path / "o.hwpx",
                  row_ops=[parse_row_op("repeat", spec)])


def test_repeat_and_append_together_keep_coordinates(form: Path, tmp_path: Path) -> None:
    """Bottom-up within a phase: the group spec addresses the post-delete table."""
    out = tmp_path / "out.hwpx"
    ops = [
        parse_row_op("repeat", "cell:0:2-4:+1"),
        parse_row_op("append", "cell:0:+1"),
        parse_row_op("delete", "cell:0:5"),
    ]
    fill_form(form, {"cell:0:5:0": "Ⅱ. 복제"}, output=out, row_ops=ops)
    _assert_consistent(out)
    firsts = [c.text for c in dump_grid(out) if c.col == 0]
    assert firsts == ["구분", "구분", "Ⅰ. 대분류", "소", "소", "Ⅱ. 복제", "", "", "끝", ""]


def test_overlapping_repeat_and_append_refused(form: Path, tmp_path: Path) -> None:
    ops = [parse_row_op("repeat", "cell:0:5-6:+1"), parse_row_op("append", "cell:0:+1")]
    with pytest.raises(RowOpError, match="overlaps"):
        fill_form(form, {}, output=tmp_path / "o.hwpx", row_ops=ops)


def test_cli_repeat_group(form: Path, tmp_path: Path, capsys) -> None:
    from hwp_agent.cli.main import main

    out = tmp_path / "cli.hwpx"
    rc = main([
        "form", "fill", str(form), "--repeat-group", "cell:0:2-4:+1",
        "--set", "cell:0:5:0=Ⅱ. 둘째", "-o", str(out),
    ])
    assert rc == 0
    assert "rows 2-4 ×1 → new rows 5-7" in capsys.readouterr().out
    _assert_consistent(out)
    rc = main(["form", "fill", str(form), "--repeat-group", "cell:0:2-3:+1",
               "-o", str(tmp_path / "bad.hwpx")])
    assert rc == 2
    assert "crosses the range boundary" in capsys.readouterr().err
    assert not (tmp_path / "bad.hwpx").exists()


def test_row_heights_resolved_when_rows_are_only_merged() -> None:
    """A row covered solely by a multi-row merge still gets a height (PR #46 review)."""
    from hwp_agent.ops.form import _row_heights

    def tc(row: int, span: int, h: int) -> str:
        return (
            f'<hp:tc><hp:cellAddr colAddr="0" rowAddr="{row}"/>'
            f'<hp:cellSpan colSpan="1" rowSpan="{span}"/>'
            f'<hp:cellSz width="100" height="{h}"/></hp:tc>'
        )

    _HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    tbl = etree.fromstring(
        f'<hp:tbl xmlns:hp="{_HP}"><hp:tr>{tc(0, 2, 3000)}</hp:tr><hp:tr/></hp:tbl>'.encode()
    )
    assert _row_heights(tbl) == [1500, 1500]
