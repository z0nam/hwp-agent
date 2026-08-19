"""Tests for hwp_agent.ops.normalize (flat-template normalizer, backlog item H).

All fixtures are synthetic (built in-test) — the real JI templates the module
was designed against are internal documents and stay out of the repo; their
exact style shape (19 styles, 로마자/'1.'/'1)' ladder, ￭/⦁/- bullets, the '-'
lookalike) is mirrored by the synthetic data below.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops.normalize import (
    _declare_in_header,
    apply_normalization,
    plan_normalization,
    propose_bullet_ladder,
    propose_heading_ladder,
)
from hwp_agent.ops.styles import (
    StyleInfo,
    _role_override,
    classify_document,
    enumerator_class,
    role_map,
)

# --------------------------------------------------------------------------- #
# synthetic StyleInfo sets (PoC shape: 정책과제_서식.hwpx)
# --------------------------------------------------------------------------- #

def _info(sid, name, *, eng="", heading="NONE", level=None, use=1, stype="PARA"):
    return StyleInfo(
        style_id=sid, name=name, eng_name=eng, para_pr_id=sid,
        heading_type=heading, outline_level=level, use_count=use,
        style_type=stype,
    )


BODY = _info("0", "바탕글", eng="Normal", use=268)
POC_INFOS = [
    BODY,
    _info("1", "로마자"),
    _info("2", "1.", use=3),
    _info("3", "1)", use=2),
    _info("4", "￭ ", heading="BULLET", level=0, use=17),
    _info("5", "- ", eng="square", heading="BULLET", level=2, use=8),
    _info("6", "⦁", heading="BULLET", level=0, use=4),
    _info("12", "쪽 번호", eng="Page Number", stype="CHAR", use=5),
    _info("13", "-", eng="bar", use=20),
    _info("16", "표내용(주요내용-개요1)", heading="BULLET", level=0, use=0),
]
POC_SIZES = {"0": 10.0, "1": 20.0, "2": 13.0, "3": 11.0,
             "4": 11.0, "5": 10.5, "6": 10.5, "12": 10.0, "13": 10.0}
# style 6's real bullet char is a PUA codepoint — the name (⦁) must classify it
POC_GLYPHS = {"4": "￭", "5": "-", "6": ""}


def _sizes(table):
    return lambda sid: table.get(sid)


# --------------------------------------------------------------------------- #
# heading ladder heuristic
# --------------------------------------------------------------------------- #

def test_heading_ladder_poc_shape() -> None:
    actions, skips, warnings = propose_heading_ladder(
        POC_INFOS, _sizes(POC_SIZES), body_id="0"
    )
    assert [(a.style_id, a.role) for a in actions] == [
        ("1", "HEADING_1"), ("2", "HEADING_2"), ("3", "HEADING_3"),
    ]
    assert not warnings
    # the CHAR style and the '-' lookalike must not be heading candidates
    assert all(a.style_id not in {"12", "13"} for a in actions)


def test_heading_ladder_aborts_on_size_tie() -> None:
    infos = [BODY, _info("1", "로마자"), _info("2", "1.")]
    sizes = {"0": 10.0, "1": 13.0, "2": 13.0}
    actions, _, warnings = propose_heading_ladder(infos, _sizes(sizes), body_id="0")
    assert actions == [] and warnings


def test_heading_ladder_aborts_on_duplicate_enum_class() -> None:
    infos = [BODY, _info("1", "1."), _info("2", "2.")]  # both DECIMAL_DOT
    sizes = {"0": 10.0, "1": 20.0, "2": 13.0}
    actions, _, warnings = propose_heading_ladder(infos, _sizes(sizes), body_id="0")
    assert actions == [] and warnings


def test_heading_ladder_needs_two_candidates() -> None:
    infos = [BODY, _info("1", "로마자")]
    actions, skips, warnings = propose_heading_ladder(
        infos, _sizes({"0": 10.0, "1": 20.0}), body_id="0"
    )
    assert actions == []
    assert any(s.style_id == "1" for s in skips)


def test_heading_ladder_aborts_when_used_outline_exists() -> None:
    infos = POC_INFOS + [_info("20", "제목 1", heading="OUTLINE", level=0, use=4)]
    actions, _, warnings = propose_heading_ladder(infos, _sizes(POC_SIZES), body_id="0")
    assert actions == [] and warnings


def test_heading_candidate_must_outsize_body() -> None:
    infos = [BODY, _info("1", "로마자"), _info("2", "1."), _info("3", "1)")]
    sizes = {"0": 10.0, "1": 20.0, "2": 13.0, "3": 10.0}  # '1)' == body size
    actions, skips, _ = propose_heading_ladder(infos, _sizes(sizes), body_id="0")
    assert [a.style_id for a in actions] == ["1", "2"]
    assert any(s.style_id == "3" for s in skips)


def test_enumerator_class_lexicon_and_patterns() -> None:
    assert enumerator_class("로마자") == "ROMAN"
    assert enumerator_class("Ⅰ.") == "ROMAN"
    assert enumerator_class("1.") == "DECIMAL_DOT"
    assert enumerator_class("1)") == "DECIMAL_PAREN"
    assert enumerator_class("가.") == "HANGUL"
    assert enumerator_class("바탕글") is None
    assert enumerator_class("-") is None


# --------------------------------------------------------------------------- #
# bullet ladder heuristic
# --------------------------------------------------------------------------- #

def test_bullet_ladder_poc_shape_glyph_order_beats_size_tie() -> None:
    actions, _, warnings = propose_bullet_ladder(
        POC_INFOS, _sizes(POC_SIZES), POC_GLYPHS.get
    )
    # ￭ (square, 11pt) > ⦁ (circle via NAME fallback, 10.5) > - (dash, 10.5)
    # > '-' (glyph-named plain style, 10pt — JI manual-bullet-head convention)
    assert [(a.style_id, a.role) for a in actions] == [
        ("4", "BULLET_1"), ("6", "BULLET_2"), ("5", "BULLET_3"), ("13", "BULLET_4"),
    ]
    assert not warnings
    # unused bullet style 16 is never a candidate
    assert all(a.style_id != "16" for a in actions)


def test_bullet_ladder_aborts_on_full_tie() -> None:
    infos = [
        _info("4", "￭ ", heading="BULLET", level=0, use=17),
        _info("5", "▪ ", heading="BULLET", level=1, use=8),
    ]
    sizes = {"4": 11.0, "5": 11.0}  # same glyph class (square) + same size
    actions, _, warnings = propose_bullet_ladder(
        infos, _sizes(sizes), {"4": "￭", "5": "▪"}.get
    )
    assert actions == [] and warnings


def test_glyph_named_plain_style_joins_ladder_with_convention_note() -> None:
    """JI 관행: 글리프 이름의 일반 스타일은 수동 불릿 대가리 → 사다리에 포함."""
    actions, _, _ = propose_bullet_ladder(POC_INFOS, _sizes(POC_SIZES), POC_GLYPHS.get)
    lookalike = next(a for a in actions if a.style_id == "13")
    assert lookalike.role == "BULLET_4"
    assert "수동 불릿 관행" in lookalike.rationale
    # but a multi-char dash-leading name is NOT a glyph name → not a candidate
    infos = POC_INFOS + [_info("14", "-기타 양식", use=3)]
    actions2, _, _ = propose_bullet_ladder(infos, _sizes(POC_SIZES), POC_GLYPHS.get)
    assert all(a.style_id != "14" for a in actions2)


# --------------------------------------------------------------------------- #
# AI: naming override alias
# --------------------------------------------------------------------------- #

def test_role_override_h_alias() -> None:
    assert _role_override(_info("9", "AI:H1")) == "HEADING_1"
    assert _role_override(_info("9", "AI:H12")) == "HEADING_12"
    assert _role_override(_info("9", "AI:HEADING_2")) == "HEADING_2"
    assert _role_override(_info("9", "AI:BODY")) == "BODY"
    assert _role_override(_info("9", "AI:BULLET_1")) == "BULLET_1"
    assert _role_override(_info("9", "바탕글")) is None


# --------------------------------------------------------------------------- #
# header byte substitution
# --------------------------------------------------------------------------- #

HEADER_3STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
    '<hh:styles itemCnt="3">'
    '<hh:style id="0" type="PARA" name="바탕글" engName="Normal"/>'
    '<hh:style id="1" type="PARA" name="로마자" engName=""/>'
    '<hh:style id="2" type="PARA" name="1."/>'
    "</hh:styles></hh:head>"
)


def _action(sid, name, role):
    from hwp_agent.ops.normalize import NormalizeAction

    return NormalizeAction(
        style_id=sid, name=name, old_eng_name="", role=role,
        declaration=f"AI:{role}", size=None, rationale="",
    )


def test_declare_in_header_replaces_and_inserts_engname() -> None:
    out = _declare_in_header(
        HEADER_3STYLES,
        [_action("1", "로마자", "HEADING_1"), _action("2", "1.", "HEADING_2")],
    )
    assert '<hh:style id="1" type="PARA" name="로마자" engName="AI:HEADING_1"/>' in out
    # style 2 had no engName attribute → inserted
    assert 'id="2" type="PARA" name="1." engName="AI:HEADING_2"/>' in out
    # untargeted style untouched
    assert '<hh:style id="0" type="PARA" name="바탕글" engName="Normal"/>' in out
    # nothing but the two targeted tags changed
    assert out.replace('engName="AI:HEADING_1"', 'engName=""').replace(
        ' engName="AI:HEADING_2"', ""
    ) == HEADER_3STYLES


def test_declare_in_header_unknown_id_raises() -> None:
    with pytest.raises(ValueError):
        _declare_in_header(HEADER_3STYLES, [_action("99", "x", "HEADING_1")])


# --------------------------------------------------------------------------- #
# end-to-end on a synthetic flat hwpx
# --------------------------------------------------------------------------- #

_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_HS = "http://www.hancom.co.kr/hwpml/2011/section"
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def make_flat_hwpx(path: Path) -> None:
    """A minimal flat template the hwpx library can open: the PoC ladder shape."""
    header = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hh:head xmlns:hh="{_HH}" version="1.4" secCnt="1">
 <hh:refList>
  <hh:charProperties itemCnt="4">
   <hh:charPr id="0" height="1000"/><hh:charPr id="1" height="2000"/>
   <hh:charPr id="2" height="1300"/><hh:charPr id="3" height="1100"/>
  </hh:charProperties>
  <hh:bullets itemCnt="2">
   <hh:bullet id="0" char="￭"><hh:paraHead level="0"/></hh:bullet>
   <hh:bullet id="1" char="-"><hh:paraHead level="2"/></hh:bullet>
  </hh:bullets>
  <hh:paraProperties itemCnt="4">
   <hh:paraPr id="0"><hh:heading type="NONE" idRef="0" level="0"/></hh:paraPr>
   <hh:paraPr id="1"><hh:heading type="NONE" idRef="0" level="0"/></hh:paraPr>
   <hh:paraPr id="2"><hh:heading type="BULLET" idRef="0" level="0"/></hh:paraPr>
   <hh:paraPr id="3"><hh:heading type="BULLET" idRef="1" level="2"/></hh:paraPr>
  </hh:paraProperties>
  <hh:styles itemCnt="5">
   <hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="0" charPrIDRef="0"/>
   <hh:style id="1" type="PARA" name="로마자" engName="" paraPrIDRef="1" charPrIDRef="1"/>
   <hh:style id="2" type="PARA" name="1." engName="" paraPrIDRef="1" charPrIDRef="2"/>
   <hh:style id="3" type="PARA" name="￭ " engName="" paraPrIDRef="2" charPrIDRef="3"/>
   <hh:style id="4" type="PARA" name="- " engName="square" paraPrIDRef="3" charPrIDRef="0"/>
  </hh:styles>
 </hh:refList>
</hh:head>'''
    def para(pid: int, pp: int, style: int, char: int, text: str) -> str:
        return (
            f'<hp:p id="{pid}" paraPrIDRef="{pp}" styleIDRef="{style}">'
            f'<hp:run charPrIDRef="{char}"><hp:t>{text}</hp:t></hp:run></hp:p>'
        )

    section = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<hs:sec xmlns:hs="{_HS}" xmlns:hp="{_HP}">'
        + para(1, 0, 0, 0, "본문")
        + para(2, 1, 1, 1, "Ⅰ. 장")
        + para(3, 1, 2, 2, "1. 절")
        + para(4, 2, 3, 3, "항목")
        + para(5, 3, 4, 0, "세부")
        + "</hs:sec>"
    )
    version = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
        'tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="1" '
        'buildNumber="0" os="10" xmlVersion="1.4" application="Hancom" appVersion="11"/>'
    )
    hpf = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" version="" '
        'unique-identifier="" id=""><opf:manifest>'
        '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
        '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
        '</opf:manifest><opf:spine><opf:itemref idref="header"/>'
        '<opf:itemref idref="section0"/></opf:spine></opf:package>'
    )
    container = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<ocf:rootfiles><ocf:rootfile full-path="Contents/content.hpf" '
        'media-type="application/hwpml-package+xml"/></ocf:rootfiles></ocf:container>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        mi = zipfile.ZipInfo("mimetype")
        mi.compress_type = zipfile.ZIP_STORED
        z.writestr(mi, "application/hwp+zip")
        z.writestr("version.xml", version)
        z.writestr("META-INF/container.xml", container)
        z.writestr("Contents/content.hpf", hpf)
        z.writestr("Contents/header.xml", header)
        z.writestr("Contents/section0.xml", section)


def test_normalize_end_to_end(tmp_path: Path) -> None:
    src = tmp_path / "flat.hwpx"
    out = tmp_path / "flat.normalized.hwpx"
    make_flat_hwpx(src)

    assert classify_document(src) == "flat"
    plan = plan_normalization(src)
    assert [(a.style_id, a.declaration) for a in plan.actions] == [
        ("1", "AI:HEADING_1"), ("2", "AI:HEADING_2"),
        ("3", "AI:BULLET_1"), ("4", "AI:BULLET_2"),
    ]
    assert plan.classification_before == "flat"
    assert plan.classification_expected == "structured"

    apply_normalization(src, plan, out)

    # declared ladder flips classify and fills the role map
    assert classify_document(out) == "structured"
    roles = role_map(out)
    assert roles["HEADING_1"] == "1" and roles["HEADING_2"] == "2"
    assert roles["BULLET_1"] == "3" and roles["BULLET_2"] == "4"
    assert roles["BODY"] == "0"

    # container preserved: same entries/order/compression, only header differs
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
        ia, ib = a.infolist(), b.infolist()
        assert [i.filename for i in ia] == [i.filename for i in ib]
        assert [i.compress_type for i in ia] == [i.compress_type for i in ib]
        diff = [
            i.filename
            for i, j in zip(ia, ib, strict=True)
            if a.read(i.filename) != b.read(j.filename)
        ]
        assert diff == ["Contents/header.xml"]
        assert ib[0].filename == "mimetype"
        assert ib[0].compress_type == zipfile.ZIP_STORED
        # the Korean names a human sees in Hangul are untouched
        header = b.read("Contents/header.xml").decode("utf-8")
        assert 'name="로마자" engName="AI:HEADING_1"' in header

    # idempotent: re-planning the output proposes nothing
    plan2 = plan_normalization(out)
    assert plan2.actions == []
    assert {d["role"] for d in plan2.already_declared} >= {
        "HEADING_1", "HEADING_2", "BULLET_1", "BULLET_2",
    }


def test_apply_with_empty_plan_raises(tmp_path: Path) -> None:
    src = tmp_path / "flat.hwpx"
    make_flat_hwpx(src)
    plan = plan_normalization(src)
    plan.actions = []
    with pytest.raises(ValueError):
        apply_normalization(src, plan, tmp_path / "out.hwpx")


def test_apply_style_roles_explicit(tmp_path: Path) -> None:
    """Explicit styleName→ROLE mapping declares engName deterministically.

    For WYSIWYG-styled forms whose names don't encode an enumerator, the caller
    supplies the mapping and it is written verbatim — no inference. Here we even
    map the '￭'/'-' bullet styles to BULLET_1/2 by name, ignoring glyph rank.
    """
    from hwp_agent.ops.normalize import apply_style_roles

    src = tmp_path / "flat.hwpx"
    out = tmp_path / "mapped.hwpx"
    make_flat_hwpx(src)

    actions = apply_style_roles(
        src, {"로마자": "HEADING_1", "￭ ": "BULLET_1", "- ": "BULLET_2"}, out
    )
    assert {(a.name, a.declaration) for a in actions} == {
        ("로마자", "AI:HEADING_1"),
        ("￭ ", "AI:BULLET_1"),
        ("- ", "AI:BULLET_2"),
    }
    roles = role_map(out)
    assert roles["HEADING_1"] == "1"
    assert roles["BULLET_1"] == "3" and roles["BULLET_2"] == "4"

    # container preserved: only header.xml changed
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
        diff = [
            i.filename
            for i, j in zip(a.infolist(), b.infolist(), strict=True)
            if a.read(i.filename) != b.read(j.filename)
        ]
        assert diff == ["Contents/header.xml"]


def test_apply_style_roles_missing_name_raises(tmp_path: Path) -> None:
    from hwp_agent.ops.normalize import apply_style_roles

    src = tmp_path / "flat.hwpx"
    make_flat_hwpx(src)
    with pytest.raises(ValueError, match="없음"):
        apply_style_roles(src, {"없는스타일": "HEADING_1"}, tmp_path / "o.hwpx")
