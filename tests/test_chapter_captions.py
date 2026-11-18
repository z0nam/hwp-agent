"""#92 — per-chapter table/figure caption numbers from literal H1 chapter labels."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from hwp_agent.ops import fill_from_markdown
from hwp_agent.ops.author import chapter_label_of, parse_markdown, _resolve_cross_refs

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLE_TPL = REPO_ROOT / "tests" / "fixtures" / "authored_table_template.hwpx"

TBL = "| 구분 | 값 |\n|---|---|\n| 가 | 1 |\n"

MD = (
    "# 요약\n\n표 제목 A\n\n" + TBL + "\n"
    "# Ⅰ. 서론\n\n표 제목 B\n\n" + TBL + "\n표 제목 C\n\n" + TBL + "\n"
    "# Ⅱ. 본론\n\n표 제목 D\n\n" + TBL + "\n"
)


def _all_sections(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return "".join(
            z.read(n).decode() for n in sorted(z.namelist())
            if n.startswith("Contents/section") and n.endswith(".xml")
        )


def test_chapter_label_of() -> None:
    assert chapter_label_of("Ⅲ. 결과") == "Ⅲ"
    assert chapter_label_of("3장 결과") == "3"
    assert chapter_label_of("2. 결과") == "2"
    assert chapter_label_of("부록 A. 설문") == "부록A"
    assert chapter_label_of("요약") is None
    assert chapter_label_of("2024 결산") is None


def test_figures_and_tables_number_per_chapter() -> None:
    blocks = parse_markdown(
        "# 요약\n\n![요약 그림](a.png)\n\n# Ⅰ. 서론\n\n![첫 그림](a.png)\n\n![둘째](b.png)\n\n"
        "# Ⅱ. 본론\n\n![셋째](c.png)\n\n표 제목\n\n" + TBL
    )
    _resolve_cross_refs(blocks, None, [], per_chapter=True)
    figs = [b.display for b in blocks if hasattr(b, "display")]
    assert figs == ["그림 1", "그림 Ⅰ-1", "그림 Ⅰ-2", "그림 Ⅱ-1"]
    tbl = next(b for b in blocks if hasattr(b, "rows"))
    assert (tbl.chapter, tbl.number) == ("Ⅱ", 1)


def test_table_captions_reset_per_chapter(tmp_path: Path) -> None:
    out = tmp_path / "o.hwpx"
    fill_from_markdown(TABLE_TPL, MD, output=out, literal_numbering=True)
    section = _all_sections(out)
    texts = [
        re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<hp:caption.*?</hp:caption>", section, re.S)
    ]
    caps = [m.group(1) for t in texts if (m := re.match(r"&lt;표 ([^&]*)&gt;", t))]
    assert caps[-4:] == ["1", "Ⅰ-1", "Ⅰ-2", "Ⅱ-1"]
