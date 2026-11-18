"""#93 — generated table cells: tight line spacing, content-based row heights."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from hwp_agent.ops import fill_from_markdown
from hwp_agent.ops.author import _text_em, _tight_cell_para_pr

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"

TABLE_MD = (
    "표 제목\n\n| 구분 | 설명 |\n|---|---|\n"
    "| 가 | " + "긴 설명 문장입니다 " * 12 + " |\n| 나 | short |\n"
)


def _parts(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as z:
        sec = "".join(
            z.read(n).decode() for n in sorted(z.namelist())
            if n.startswith("Contents/section") and n.endswith(".xml")
        )
        return z.read("Contents/header.xml").decode(), sec


def test_text_em_weights_ascii_lighter_than_cjk() -> None:
    assert _text_em("가나다") == 3.0
    assert _text_em("abcd") < 3.0


def test_tight_cell_para_pr_clones_airy_spacing_only() -> None:
    from hwpx.document import HwpxDocument

    doc = HwpxDocument.open(DEFAULT_TPL)
    pps = doc._root._headers[0]._para_properties_element(create=False)
    ns = "{http://www.hancom.co.kr/hwpml/2011/head}"
    airy = next(
        p.get("id") for p in pps.findall(f"{ns}paraPr")
        if any(int(e.get("value", "0")) > 140 for e in p.iter(f"{ns}lineSpacing"))
    )
    cache: dict[str, str] = {}
    new = _tight_cell_para_pr(doc, airy, cache)
    assert new != airy
    clone = pps.find(f"{ns}paraPr[@id='{new}']")
    assert {e.get("value") for e in clone.iter(f"{ns}lineSpacing")} == {"130"}
    again = _tight_cell_para_pr(doc, new, cache)  # already tight → unchanged
    assert again == new


def test_generated_table_height_is_sum_of_rows(tmp_path: Path) -> None:
    out = tmp_path / "o.hwpx"
    fill_from_markdown(DEFAULT_TPL, TABLE_MD, output=out)
    _, sec = _parts(out)
    tbl = re.search(r"<hp:tbl\b.*?</hp:tbl>", sec, re.S).group(0)
    rows = re.findall(r"<hp:tr>.*?</hp:tr>", tbl, re.S)
    row_h = [
        max(int(h) for h in re.findall(r'<hp:cellSz width="\d+" height="(\d+)"', r))
        for r in rows
    ]
    box = int(re.search(r'<hp:sz width="\d+" widthRelTo="\w+" height="(\d+)"', tbl).group(1))
    cap_extra = box - sum(row_h)
    assert cap_extra >= 0
    assert cap_extra < 4000  # caption + gap only, not leftover default height
    assert row_h[1] > row_h[2]  # the long-text row is taller than the short one


def test_wrapping_does_not_split_glyphs_across_lines() -> None:
    from hwp_agent.ops.author import _wrapped_lines

    assert _wrapped_lines("가" * 19, 9.5) == 3  # 9 per line → 3 lines, not ceil(19/9.5)=2
    assert _wrapped_lines("", 9.5) == 1
