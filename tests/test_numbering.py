"""#101 — uncaptioned decorations stay out of 그림·표 차례; #95 — write sets chapter wings."""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

import pytest

from hwp_agent.cli.main import main
from hwp_agent.ops import neutralize_uncaptioned_numbering

GOLDEN = Path(__file__).resolve().parents[1] / "examples" / "정책과제-template.hwpx"

pytestmark = pytest.mark.skipif(not GOLDEN.is_file(), reason="golden template absent")

OBJ = r"<hp:(?:rect|pic|tbl|line|ellipse|polygon|curve|container)\b[^>]*numberingType=\"(?:PICTURE|TABLE)\"[^>]*>"


def _uncaptioned_numbered(path: Path) -> int:
    """Numbered objects (PICTURE/TABLE) with no <hp:caption> child, across all parts."""
    from lxml import etree

    hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not (name.startswith("Contents/section") or name.startswith("Contents/masterpage")):
                continue
            root = etree.fromstring(z.read(name))
            for el in root.iter():
                if not isinstance(el.tag, str) or el.get("numberingType") not in ("PICTURE", "TABLE"):
                    continue
                if el.find(f"{{{hp}}}caption") is None:
                    n += 1
    return n


def test_committed_golden_template_has_no_numbered_decorations() -> None:
    assert _uncaptioned_numbered(GOLDEN) == 0


def test_neutralize_only_touches_uncaptioned_objects(tmp_path: Path) -> None:
    work = tmp_path / "t.hwpx"
    shutil.copyfile(GOLDEN, work)
    with zipfile.ZipFile(work) as z:  # the template keeps one captioned reference table
        before = sum(len(re.findall(r'numberingType="TABLE"', z.read(n).decode()))
                     for n in z.namelist() if n.startswith("Contents/section"))
    assert before >= 1
    changed = neutralize_uncaptioned_numbering(work)
    assert changed == 0  # the committed template is already clean (idempotent)
    # reintroduce one decoration and check it is the only thing that changes
    with zipfile.ZipFile(work) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    mp = next(n for n in parts if n.startswith("Contents/masterpage") and b"<hp:rect" in parts[n])
    parts[mp] = parts[mp].replace(b'numberingType="NONE"', b'numberingType="PICTURE"', 1)
    dirty = tmp_path / "dirty.hwpx"
    with zipfile.ZipFile(dirty, "w") as zout:
        for n, data in parts.items():
            zout.writestr(n, data)
    assert _uncaptioned_numbered(dirty) == 1
    assert neutralize_uncaptioned_numbering(dirty) == 1
    assert _uncaptioned_numbered(dirty) == 0
    with zipfile.ZipFile(dirty) as z:
        tbl_after = sum(len(re.findall(r'numberingType="TABLE"', z.read(n).decode()))
                        for n in z.namelist() if n.startswith("Contents/section"))
    assert tbl_after == before  # captioned table untouched


def test_write_sets_chapter_wings_and_keeps_decor_out_of_toc(tmp_path: Path, capsys) -> None:
    md = tmp_path / "r.md"
    md.write_text("# Ⅰ. 서론\n\n가상 본문\n\n# Ⅱ. 본론\n\n가상 본문\n", encoding="utf-8")
    out = tmp_path / "r.hwpx"
    rc = main(["write", str(md), "--template", str(GOLDEN), "--literal-numbering", "-o", str(out)])
    assert rc == 0
    assert "side wing" in capsys.readouterr().out  # wings set, like `build`
    assert _uncaptioned_numbered(out) == 0  # wing rectangles included


def test_write_can_leave_wings_alone(tmp_path: Path, capsys) -> None:
    md = tmp_path / "r.md"
    md.write_text("# Ⅰ. 서론\n\n가상 본문\n", encoding="utf-8")
    off = tmp_path / "off.hwpx"
    assert main(["write", str(md), "--template", str(GOLDEN), "--literal-numbering",
                 "--no-chapter-header", "-o", str(off)]) == 0
    assert "side wing" not in capsys.readouterr().out
    assert _uncaptioned_numbered(off) == 0  # decoration cleanup is independent
