"""Tests for hwp_agent.ops.doctor (template style-system diagnosis)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwp_agent.ops import diagnose_template

REPO_ROOT = Path(__file__).resolve().parents[1]
APPENDIX = REPO_ROOT / "tests" / "fixtures" / "sample_appendix.hwpx"


@pytest.mark.skipif(not APPENDIX.is_file(), reason="appendix fixture not present")
def test_diagnose_reports_ladders_and_findings() -> None:
    r = diagnose_template(str(APPENDIX))

    assert r["classification"] == "structured"
    assert r["styles_total"] > r["roles_mapped"]

    # heading ladder is present and font sizes are non-increasing by level
    headings = r["ladders"]["HEADING"]
    assert [h["role"] for h in headings][:1] == ["HEADING_1"]
    sizes = [h["size"] for h in headings if h["size"] is not None]
    assert sizes == sorted(sizes, reverse=True)

    # the report shape is stable for the CLI / JSON consumers
    for key in (
        "gaps", "hierarchy_violations", "unmapped_ladder_siblings",
        "unmapped_structural", "warnings",
    ):
        assert isinstance(r[key], list)


@pytest.mark.skipif(not APPENDIX.is_file(), reason="appendix fixture not present")
def test_diagnose_flags_unmapped_bullet_sibling() -> None:
    # this fixture has multiple bullet styles at the same outline level; the role
    # map keeps one, so the others must surface as un-mapped ladder siblings
    r = diagnose_template(str(APPENDIX))
    assert r["unmapped_ladder_siblings"]
    assert all("use" in e and "name" in e for e in r["unmapped_ladder_siblings"])
    assert any("role map" in w for w in r["warnings"])


@pytest.mark.skipif(not APPENDIX.is_file(), reason="appendix fixture not present")
def test_diagnose_does_not_false_flag_bullet_hierarchy() -> None:
    # bullet nesting is by glyph, not outline level — ■(10.5) over -(10) is correct,
    # not a violation, and BULLET_n gaps from the outline level are meaningless (item G)
    r = diagnose_template(str(APPENDIX))
    assert not any("BULLET" in v for v in r["hierarchy_violations"])
    assert not any(g.startswith("BULLET_") for g in r["gaps"])
    # the un-targetable bullet is surfaced with actionable AI:BULLET_n guidance
    assert any("AI:BULLET" in w for w in r["warnings"])


@pytest.mark.skipif(not APPENDIX.is_file(), reason="appendix fixture not present")
def test_check_cli_runs(capsys) -> None:
    from hwp_agent.cli.main import build_parser

    for cmd in ("check", "doctor"):  # `doctor` is kept as a back-compat alias
        args = build_parser().parse_args([cmd, str(APPENDIX)])
        assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "HEADING ladder" in out and "findings" in out
