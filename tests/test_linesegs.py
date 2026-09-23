"""Tests for hwp_agent.ops.linesegs (stale <hp:linesegarray> — issue #15)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from hwp_agent.ops.linesegs import (
    drop_stale_linesegarrays,
    iter_mismatches,
)

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _para(text_runs: list[str], nseg: int) -> str:
    """Build one <hp:p> with the given <hp:t> runs (lineBreaks written literally as
    the token ``|LB|``) and ``nseg`` <hp:lineseg> entries in its linesegarray."""
    runs = ""
    for t in text_runs:
        inner = t.replace("|LB|", "<hp:lineBreak/>")
        runs += f"<hp:run><hp:t>{inner}</hp:t></hp:run>"
    segs = "".join(f'<hp:lineseg textpos="{i}"/>' for i in range(nseg))
    lsa = f"<hp:linesegarray>{segs}</hp:linesegarray>" if nseg else ""
    return f"<hp:p>{runs}{lsa}</hp:p>"


def _make_hwpx(dst: Path, paras: list[str], *, section: str = "section0") -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<hs:sec xmlns:hp="{_HP}" '
        'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section">'
        + "".join(paras)
        + "</hs:sec>"
    )
    with zipfile.ZipFile(dst, "w") as z:
        z.writestr("mimetype", "application/hwp+zip", zipfile.ZIP_STORED)
        z.writestr(f"Contents/{section}.xml", xml)
    return dst


def test_flags_paragraph_with_too_few_linesegs(tmp_path: Path) -> None:
    """2 lineBreaks (=3 lines) but only 1 lineseg → stale (issue #15)."""
    # one stale para, one healthy para, one uncached para
    f = _make_hwpx(
        tmp_path / "a.hwpx",
        [
            _para(["줄1|LB|줄2|LB|줄3"], 1),  # 2 breaks, 1 seg → STALE
            _para(["긴 문단"], 2),  # 0 breaks, 2 segs → fine
            _para(["캐시 없음|LB|둘째"], 0),  # no linesegarray → not stale
        ],
    )
    ms = iter_mismatches(f)
    assert len(ms) == 1
    assert ms[0].linebreaks == 2 and ms[0].linesegs == 1
    assert "줄1" in ms[0].text


def test_healthy_and_uncached_are_not_flagged(tmp_path: Path) -> None:
    f = _make_hwpx(
        tmp_path / "b.hwpx",
        [
            _para(["한 줄"], 1),  # 0 breaks, 1 seg → fine
            _para(["a|LB|b"], 2),  # 1 break (=2 lines), 2 segs → fine
            _para(["x|LB|y|LB|z"], 0),  # no cache → not stale
        ],
    )
    assert iter_mismatches(f) == []


def test_nested_table_cell_paragraph_is_checked(tmp_path: Path) -> None:
    """The issue was first seen in a table cell — nested <hp:p> must be judged too."""
    cell_p = _para(["셀줄1|LB|셀줄2|LB|셀줄3"], 1)  # stale
    tbl = (
        "<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList>"
        f"{cell_p}"
        "</hp:subList></hp:tc></hp:tr></hp:tbl></hp:run>"
        # the wrapping paragraph itself has no direct t/linesegarray
        "</hp:p>"
    )
    f = _make_hwpx(tmp_path / "c.hwpx", [tbl])
    ms = iter_mismatches(f)
    assert len(ms) == 1 and ms[0].linebreaks == 2 and ms[0].linesegs == 1


def test_drop_removes_only_stale_caches(tmp_path: Path) -> None:
    src = _make_hwpx(
        tmp_path / "s.hwpx",
        [
            _para(["줄1|LB|줄2|LB|줄3"], 1),  # stale → drop its linesegarray
            _para(["멀쩡"], 1),  # healthy → keep
        ],
    )
    out = tmp_path / "o.hwpx"
    n = drop_stale_linesegarrays(src, out)
    assert n == 1
    # after the drop there are no stale paragraphs
    assert iter_mismatches(out) == []
    # the healthy paragraph still has its cache
    with zipfile.ZipFile(out) as z:
        xml = z.read("Contents/section0.xml").decode("utf-8")
    assert xml.count("<hp:linesegarray>") == 1  # only the healthy one remains


def test_drop_inplace_is_safe(tmp_path: Path) -> None:
    """src == dst must not corrupt the zip (writes via a temp)."""
    f = _make_hwpx(tmp_path / "ip.hwpx", [_para(["a|LB|b|LB|c"], 1)])
    n = drop_stale_linesegarrays(f, f)
    assert n == 1 and iter_mismatches(f) == []
    with zipfile.ZipFile(f) as z:  # still a valid, readable zip
        assert "Contents/section0.xml" in z.namelist()


def test_drop_noop_still_copies_to_output(tmp_path: Path) -> None:
    src = _make_hwpx(tmp_path / "n.hwpx", [_para(["한 줄"], 1)])
    out = tmp_path / "n_out.hwpx"
    assert drop_stale_linesegarrays(src, out) == 0
    assert out.is_file()
