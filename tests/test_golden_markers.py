"""#94 — golden template carries {{summary}}/{{intro}}/{{references}} markers."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import build_report

GOLDEN = Path(__file__).resolve().parents[1] / "examples" / "정책과제-template.hwpx"

pytestmark = pytest.mark.skipif(not GOLDEN.is_file(), reason="golden template absent")


def _section(path: Path, idx: int) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(f"Contents/section{idx}.xml").decode()


def test_build_fills_summary_intro_body_references_in_their_own_sections(tmp_path: Path) -> None:
    (tmp_path / "s.md").write_text("요약문 가상 내용입니다\n", encoding="utf-8")
    (tmp_path / "i.md").write_text("들어가며 가상 내용입니다\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Ⅰ. 서론\n\n본문 가상 내용입니다\n", encoding="utf-8")
    (tmp_path / "r.md").write_text(
        "**해외 문헌**\n\nSmith, J. (2020). A Title.\n\n**국내 문헌**\n\n홍길동 (2021). 제목.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({
            "template": str(GOLDEN),
            "output": "out.hwpx",
            "literal_numbering": True,
            "parts": {"summary": "s.md", "intro": "i.md", "body": "b.md", "references": "r.md"},
        }),
        encoding="utf-8",
    )
    result = build_report(manifest)
    assert not [w for w in result.warnings if "marker" in w], result.warnings
    out = tmp_path / "out.hwpx"
    for idx, needle in ((2, "요약문 가상"), (3, "들어가며 가상"), (4, "본문 가상"), (5, "Smith, J.")):
        assert needle in _section(out, idx), f"{needle!r} not in section{idx}"
    sec5 = _section(out, 5)
    assert "홍길동 (2021)" in sec5
    assert not re.search(r"\{\{(summary|intro|references)\}\}", "".join(
        _section(out, i) for i in range(7)
    ))
