"""Tests for hwp_agent.ops.integrity (package integrity audit, issue #22)."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops.integrity import audit_package

REPO_ROOT = Path(__file__).resolve().parents[1]
TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"

pytestmark = pytest.mark.skipif(not TPL.is_file(), reason="bundled template missing")


def _rewrite(dst: Path, edits: dict[str, callable]) -> Path:
    """Copy the bundled template to *dst*, applying text edits per part name."""
    with zipfile.ZipFile(TPL) as src, zipfile.ZipFile(dst, "w") as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename in edits:
                data = edits[info.filename](data.decode("utf-8")).encode("utf-8")
            out.writestr(info, data)
    return dst


def _kinds(path: Path) -> set[str]:
    return {i.kind for i in audit_package(path)}


def test_clean_template_has_no_issues() -> None:
    # includes Hangul's 0xFFFFFFFF "no reference" sentinel, which is not dangling
    assert audit_package(TPL) == []


def test_mid_list_insert_with_duplicate_id(tmp_path: Path) -> None:
    """The #22 shape: a charPr cloned after id 1 reusing an existing id."""

    def edit(h: str) -> str:
        m = re.search(r'<hh:charPr id="1".*?</hh:charPr>', h, re.S)
        clone = m.group(0).replace('id="1"', 'id="3"', 1)  # id 3 already exists
        h = h.replace(m.group(0), m.group(0) + clone, 1)
        return re.sub(
            r'(<hh:charProperties itemCnt=")(\d+)',
            lambda mm: mm.group(1) + str(int(mm.group(2)) + 1), h, count=1)

    p = _rewrite(tmp_path / "t.hwpx", {"Contents/header.xml": edit})
    issues = {i.kind: i for i in audit_package(p)}
    assert {"duplicate", "order"} <= set(issues)
    assert "count" not in issues  # itemCnt was kept in step
    assert "position 2 after id 1" in issues["order"].detail


def test_item_count_mismatch(tmp_path: Path) -> None:
    def edit(h: str) -> str:
        return re.sub(r'(<hh:charProperties itemCnt=")(\d+)',
                      lambda m: m.group(1) + str(int(m.group(2)) + 2), h, count=1)

    p = _rewrite(tmp_path / "t.hwpx", {"Contents/header.xml": edit})
    assert _kinds(p) == {"count"}


def test_dangling_char_reference(tmp_path: Path) -> None:
    def edit(s: str) -> str:
        return re.sub(r'charPrIDRef="\d+"', 'charPrIDRef="999"', s, count=1)

    p = _rewrite(tmp_path / "t.hwpx", {"Contents/section0.xml": edit})
    issues = [i for i in audit_package(p) if i.kind == "dangling"]
    assert issues and "999" in issues[0].detail


def test_section_without_secpr(tmp_path: Path) -> None:
    def edit(s: str) -> str:
        return re.sub(r"<hp:secPr\b.*?</hp:secPr>", "", s, count=1, flags=re.S)

    p = _rewrite(tmp_path / "t.hwpx", {"Contents/section0.xml": edit})
    issues = [i for i in audit_package(p) if i.kind == "no-secPr"]
    assert [i.where for i in issues] == ["Contents/section0.xml"]


def test_check_surfaces_integrity_findings(tmp_path: Path) -> None:
    from hwp_agent.ops import diagnose_template

    def edit(s: str) -> str:
        return re.sub(r"<hp:secPr\b.*?</hp:secPr>", "", s, count=1, flags=re.S)

    p = _rewrite(tmp_path / "t.hwpx", {"Contents/section0.xml": edit})
    r = diagnose_template(str(p))
    assert any(e["kind"] == "no-secPr" for e in r["integrity"])
    assert any(w.startswith("integrity [no-secPr]") for w in r["warnings"])


def test_dangling_reference_in_master_page(tmp_path: Path) -> None:
    """PR #26 review: master pages (page headers/footers) are scanned too."""
    with zipfile.ZipFile(TPL) as z:
        mps = [n for n in z.namelist() if n.startswith("Contents/masterpage")]
        mp = next((n for n in mps if b"charPrIDRef" in z.read(n)), None)
    if mp is None:
        pytest.skip("template has no master page with charPr references")

    def edit(s: str) -> str:
        return re.sub(r'charPrIDRef="\d+"', 'charPrIDRef="888"', s, count=1)

    p = _rewrite(tmp_path / "t.hwpx", {mp: edit})
    issues = [i for i in audit_package(p) if i.kind == "dangling"]
    assert issues and "888" in issues[0].detail
