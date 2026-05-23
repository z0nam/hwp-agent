"""Tests for hwp_agent.ops.styles (role map + document classifier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwp_agent.ops import classify_document, read_style_system, role_map

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
TYPE1 = FIXTURES / "sample_hwpx.hwpx"  # Hancom-authored, real outline styles
REF = FIXTURES / "sample_big_ref.hwpx"


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_role_map_maps_outline_styles_to_headings() -> None:
    roles = role_map(TYPE1)
    # outline-styled document → heading roles + body resolved
    assert "HEADING_1" in roles
    assert "HEADING_2" in roles
    assert "BODY" in roles

    infos = {i.style_id: i for i in read_style_system(TYPE1)}
    # HEADING_1 must map to an OUTLINE level-0 style
    h1 = infos[roles["HEADING_1"]]
    assert h1.heading_type == "OUTLINE" and h1.outline_level == 0
    # BODY must map to a non-heading style
    assert infos[roles["BODY"]].heading_type == "NONE"


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_prefers_canonical_outline_engname() -> None:
    # sample_hwpx has two level-0 outline styles ('로마자' and 'JI_장제목'/Outline 1);
    # the canonical engName "Outline 1" should win for HEADING_1.
    roles = role_map(TYPE1)
    infos = {i.style_id: i for i in read_style_system(TYPE1)}
    assert infos[roles["HEADING_1"]].eng_name.lower() == "outline 1"


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_classify_structured() -> None:
    assert classify_document(TYPE1) == "structured"


@pytest.mark.skipif(not REF.is_file(), reason="reference not present")
def test_classify_reference_structured() -> None:
    assert classify_document(REF) == "structured"
