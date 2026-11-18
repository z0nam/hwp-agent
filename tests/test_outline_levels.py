"""#97 — literal-numbering headings get OUTLINE levels bound to a blank numbering."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from hwp_agent.ops import fill_from_markdown

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"

MD = "# Ⅰ. 서론\n\n본문입니다\n\n## 1. 배경\n\n본문\n\n### 1) 세부\n\n본문\n"


def _parts(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as z:
        return z.read("Contents/header.xml").decode(), z.read("Contents/section0.xml").decode()


def test_literal_numbering_headings_become_outline_levels(tmp_path: Path) -> None:
    out = tmp_path / "o.hwpx"
    fill_from_markdown(DEFAULT_TPL, MD, output=out, literal_numbering=True)
    header, section = _parts(out)

    blank = [
        m.group(1)
        for m in re.finditer(r'<hh:numbering id="(\d+)".*?</hh:numbering>', header, re.S)
        if not re.search(r">\s*\S[^<]*</hh:paraHead>", m.group(0))
    ]
    assert len(blank) == 1
    num_id = blank[0]
    levels = re.findall(
        rf'<hh:heading type="OUTLINE" idRef="{num_id}" level="(\d)"', header
    )
    assert set(levels) >= {"0", "1", "2"}
    # the section's outline definition is the blank one the headings point at
    assert f'outlineShapeIDRef="{num_id}"' in section
    # literal numbers stay in the text
    assert "Ⅰ. 서론" in section


def test_existing_auto_numbered_section_keeps_its_outline(tmp_path: Path) -> None:
    """Appending literal-numbered content to a section that already has
    auto-numbered headings must not blank those numbers (Codex review on #99)."""
    first = tmp_path / "a.hwpx"
    fill_from_markdown(DEFAULT_TPL, "# 서론\n\n본문\n", output=first)
    second = tmp_path / "b.hwpx"
    fill_from_markdown(first, MD, output=second, literal_numbering=True)
    header, section = _parts(second)
    ref = re.search(r'outlineShapeIDRef="(\d+)"', section).group(1)
    m = re.search(rf'<hh:numbering id="{ref}".*?</hh:numbering>', header, re.S)
    assert re.search(r">\s*\S[^<]*</hh:paraHead>", m.group(0))  # still a real numbering
