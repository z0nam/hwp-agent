"""Cover/colophon slots + metadata fill (issue #32, #51 ④).

B1: a house form's 표지·판권 example strings are baked into ``{{slots}}`` from the
per-form ``overrides/<form>.json`` ``meta_slots`` map. B2: ``build``/``write`` fill
them from ``meta.json`` / Markdown front matter via ``form fill``, and set
``content.hpf``'s title/creator. Covered here: meta present / absent / partial.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest
from hwpx.document import HwpxDocument
from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops import build_report, diagnose_template, read_metadata
from hwp_agent.ops.report_meta import (
    apply_report_meta,
    bake_meta_slots,
    normalize_meta,
    publisher_for,
    residual_tokens,
    split_front_matter,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
HOUSE = REPO_ROOT / "examples" / "house-forms"
STRATEGY = HOUSE / "전략과제_서식.normalized.hwpx"
FORMS = ("정책과제", "전략과제", "기반과제", "센터과제")
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

# fictitious people/numbers only — no real personal data in tests
META = {
    "title": "가상 연구의\n두 줄 제목",
    "report_series": "미래기획연구",
    "report_number": "2099-01",
    "lead_researcher": "홍길동  제주연구원 연구위원",
    "co_researchers": ["갑을병  제주연구원 연구위원", "정무기  제주연구원 위촉연구원"],
    "pub_date": "2099-08",
    "publisher": "가 나 다",
    "printer": "가상인쇄",
    "isbn": "979-11-00000-00-0   00000",
}


def _texts(path: Path, section: int) -> list[str]:
    """Visible text of each paragraph (direct runs; line breaks as \\n)."""
    doc = HwpxDocument.open(str(path))
    out = []
    for p in doc.sections[section].element.iter(f"{_HP}p"):
        s = ""
        for t in (t for r in p.findall(f"{_HP}run") for t in r.findall(f"{_HP}t")):
            s += t.text or ""
            for c in t:
                s += ("\n" if c.tag == f"{_HP}lineBreak" else "") + (c.tail or "")
        if s.strip():
            out.append(s)
    return out


def _build(tmp_path: Path, meta=None, body: str = "# Ⅰ. 서론\n\n본문.\n", **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "body.md").write_text(body, encoding="utf-8")
    manifest = {"template": str(STRATEGY), "output": "out.hwpx", "parts": {"body": "body.md"}}
    if meta is not None:
        (tmp_path / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
        manifest["meta"] = "meta.json"
    (tmp_path / "m.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    res = build_report(tmp_path / "m.json", **kw)
    return res, tmp_path / "out.hwpx"


# --------------------------------------------------------------------------- #
# B1 — bake
# --------------------------------------------------------------------------- #
def _split_run_template(tmp_path: Path) -> Path:
    """Default template + a cover paragraph whose example spans two runs."""
    doc = HwpxDocument.open(str(DEFAULT_TPL))
    p = doc.sections[0].add_paragraph("연구책임   ", style_id_ref=0, para_pr_id_ref=0)
    run = p.element.find(f"{_HP}run")
    run.set("charPrIDRef", "1")
    run2 = etree.SubElement(p.element, f"{_HP}run", charPrIDRef="2")
    etree.SubElement(run2, f"{_HP}t").text = "강영"
    run3 = etree.SubElement(p.element, f"{_HP}run", charPrIDRef="2")
    etree.SubElement(run3, f"{_HP}t").text = "준 연구위원"
    doc.sections[0].mark_dirty()
    out = tmp_path / "t.hwpx"
    doc.save_to_path(str(out))
    return out


def test_bake_replaces_across_runs_and_keeps_label_style(tmp_path: Path) -> None:
    tpl = _split_run_template(tmp_path)
    cfg = {"cover": {"section": 0, "replace": {"강영준 연구위원": "{{lead_researcher}}"}}}
    assert bake_meta_slots(tpl, cfg) == ["s0:{{lead_researcher}}"]
    doc = HwpxDocument.open(str(tpl))
    p = next(
        p for p in doc.sections[0].element.iter(f"{_HP}p")
        if "{{lead_researcher}}" in "".join(p.itertext())
    )
    runs = [(r.get("charPrIDRef"), "".join(r.itertext())) for r in p.findall(f"{_HP}run")]
    # the label run keeps its own style; the token takes the value run's style
    assert runs[0] == ("1", "연구책임   ")
    assert runs[1] == ("2", "{{lead_researcher}}")
    assert "".join(t for _, t in runs) == "연구책임   {{lead_researcher}}"
    # idempotent: baking a baked copy changes nothing
    assert bake_meta_slots(tpl, cfg) == []


def test_bake_fails_loudly_when_example_changed(tmp_path: Path) -> None:
    tpl = _split_run_template(tmp_path)
    cfg = {"cover": {"section": 0, "replace": {"없는 예시 문자열": "{{title}}"}}}
    with pytest.raises(ValueError, match="없는 예시 문자열"):
        bake_meta_slots(tpl, cfg)


@pytest.mark.parametrize("form", FORMS)
def test_house_forms_carry_meta_slots(form: str) -> None:
    """The committed baked forms match their override config (re-bake = no-op)."""
    path = HOUSE / f"{form}_서식.normalized.hwpx"
    if not path.is_file():
        pytest.skip("baked house form not present")
    cfg = json.loads((REPO_ROOT / "overrides" / f"{form}_서식.hwpx.json").read_text("utf-8"))
    series = cfg["meta_slots"]["cover"]["replace"]
    series_token = next(v for v in series.values() if "report_series" in v)
    cover, colophon = _texts(path, 0), _texts(path, -1)
    assert cover == [series_token, "{{title}}", "{{subtitle}}", "{{authors}}"]
    assert series_token in colophon and "ISBN      {{isbn}}" in colophon
    assert "연구책임   {{lead_researcher}}" in colophon
    assert "인 쇄 처    {{printer}}" in colophon
    # doctor treats them as fill slots, not ambiguous markers
    assert diagnose_template(path)["warnings"] == []


def test_refresh_bake_one_applies_meta_slots(tmp_path: Path) -> None:
    """scripts/refresh-house-forms.py bakes the slots from the override config."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "refresh_house_forms", REPO_ROOT / "scripts" / "refresh-house-forms.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tpl = _split_run_template(tmp_path)
    out = tmp_path / "baked.hwpx"
    cfg = {"meta_slots": {"cover": {"section": 0, "replace": {"강영준": "{{authors}}"}}}}
    summary = mod.bake_one(tpl, out, tmp_path, config=cfg)
    assert summary["meta_slots"] == 1
    assert any("{{authors}}" in t for t in _texts(out, 0))


# --------------------------------------------------------------------------- #
# B2 — fill (meta present / absent / partial)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_build_fills_cover_and_colophon(tmp_path: Path) -> None:
    res, out = _build(tmp_path, META)
    cover, colophon = _texts(out, 0), _texts(out, -1)
    assert cover == ["미래기획연구 2099-01", "가상 연구의\n두 줄 제목", "홍길동"]
    assert "연구책임   홍길동  제주연구원 연구위원" in colophon
    assert "공동연구   갑을병  제주연구원 연구위원" in colophon
    # continuation line: label replaced by same-width blanks
    assert "　　　　   정무기  제주연구원 위촉연구원" in colophon
    assert "발 행 일    2099년 8월" in colophon
    assert "발 행 인    가 나 다" in colophon
    assert "인 쇄 처    가상인쇄" in colophon
    assert "ISBN      979-11-00000-00-0   00000" in colophon
    assert residual_tokens(out) == []
    assert res.meta["missing"] == [] and res.meta["blank"] == ["subtitle"]
    assert not any("{{" in w for w in res.warnings)
    md = read_metadata(out)
    assert md.title == "가상 연구의 두 줄 제목"
    assert md.creator == "홍길동"
    assert diagnose_template(out)["integrity"] == []


@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_build_keeps_colophon_height_when_adding_lines(tmp_path: Path) -> None:
    """Each extra 공동연구 line takes out one empty spacer paragraph (one-page 판권)."""

    def paras(path: Path) -> int:
        return len(HwpxDocument.open(str(path)).sections[-1].element.findall(f"{_HP}p"))

    _, one = _build(tmp_path / "a", {**META, "co_researchers": META["co_researchers"][:1]})
    _, two = _build(tmp_path / "b", META)
    assert paras(two) == paras(one)


@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_build_partial_meta_blanks_and_warns(tmp_path: Path) -> None:
    meta = {"title": "가상 제목", "lead_researcher": "홍길동  제주연구원 연구위원"}
    res, out = _build(tmp_path, meta)
    colophon = _texts(out, -1)
    assert residual_tokens(out) == []  # never ships a literal {{…}}
    assert "전략연구" in " ".join(_texts(out, 0))  # form default series
    assert "발 행 일    " in colophon and "ISBN      " in colophon  # labels stay
    assert not any(t.startswith("공동연구") for t in colophon)  # no co → line dropped
    assert set(res.meta["missing"]) == {"report_number", "pub_date", "publisher", "isbn"}
    assert any("필수 항목" in w and "isbn" in w for w in res.warnings)


@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_build_without_meta_keeps_slots_and_warns(tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"name": "프로필이름"}, ensure_ascii=False), "utf-8")
    monkeypatch.setenv("HWP_AGENT_PROFILE", str(profile))
    res, out = _build(tmp_path, None, body="# 첫 제목\n\n본문.\n")
    assert "{{title}}" in residual_tokens(out)  # left for a later form fill
    assert any("표지·판권 슬롯" in w for w in res.warnings)
    assert not any("unconsumed template marker" in w for w in res.warnings)
    md = read_metadata(out)
    assert md.title == "첫 제목"  # md first heading
    assert md.creator == "프로필이름"  # profile name (only the name is read)


@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_author_flag_beats_profile(tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"name": "프로필이름"}, ensure_ascii=False), "utf-8")
    monkeypatch.setenv("HWP_AGENT_PROFILE", str(profile))
    _, out = _build(tmp_path, None, author="플래그작성자")
    assert read_metadata(out).creator == "플래그작성자"


@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_build_reads_front_matter_and_strips_it(tmp_path: Path) -> None:
    body = (
        "---\n"
        "제목: 프런트매터 제목\n"
        "report_number: 2099-02\n"
        "연구책임: 홍길동  제주연구원 연구위원\n"
        "co_researchers:\n"
        "  - 갑을병  제주연구원 연구위원\n"
        "end_date: 2026-10-06\n"
        "---\n"
        "# Ⅰ. 서론\n\n본문.\n"
    )
    res, out = _build(tmp_path, None, body=body)
    cover = _texts(out, 0)
    assert "프런트매터 제목" in cover and "전략연구 2099-02" in cover
    assert "발 행 인    강 창 민" in _texts(out, -1)  # derived from end_date
    body_text = " ".join(" ".join(_texts(out, i)) for i in range(1, 6))
    assert "report_number" not in body_text and "프런트매터" not in body_text


def test_write_unrelated_front_matter_is_not_meta(tmp_path: Path) -> None:
    """Bookkeeping front matter (문서명·작성일) neither fills nor blanks slots."""
    if not STRATEGY.is_file():
        pytest.skip("baked house form not present")
    md = tmp_path / "t.md"
    md.write_text("---\n문서명: 메모\n작성일: 2099-01-01\n---\n# 제목\n\n본문.\n", "utf-8")
    out = tmp_path / "o.hwpx"
    assert main(["write", str(md), "--template", str(STRATEGY), "-o", str(out)]) == 0
    assert "{{title}}" in residual_tokens(out)
    assert "문서명" not in " ".join(" ".join(_texts(out, i)) for i in range(7))


def test_write_sets_document_info_on_default_template(tmp_path: Path) -> None:
    """#51 ④: opf:title is no longer the template's 'CONTENTS'."""
    md = tmp_path / "t.md"
    md.write_text("# 보고서 제목\n\n본문.\n", "utf-8")
    out = tmp_path / "o.hwpx"
    rc = main(["write", str(md), "--chapter", "3", "--author", "작성자", "-o", str(out)])
    assert rc == 0
    with zipfile.ZipFile(out) as z:
        hpf = z.read("Contents/content.hpf").decode("utf-8")
    assert "<opf:title>보고서 제목</opf:title>" in hpf
    assert read_metadata(out).creator == "작성자"


def test_write_meta_flag_fills_house_form(tmp_path: Path) -> None:
    if not STRATEGY.is_file():
        pytest.skip("baked house form not present")
    md = tmp_path / "t.md"
    md.write_text("# Ⅰ. 서론\n\n본문.\n", "utf-8")
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps(META, ensure_ascii=False), "utf-8")
    out = tmp_path / "o.hwpx"
    rc = main(["write", str(md), "--template", str(STRATEGY), "--meta", str(meta), "-o", str(out)])
    assert rc == 0
    assert residual_tokens(out) == []
    assert "홍길동" in _texts(out, 0)


def test_apply_meta_on_golden_template(tmp_path: Path) -> None:
    """The hand-curated 정책과제 golden template's slots fill the same way."""
    golden = REPO_ROOT / "examples" / "정책과제-template.hwpx"
    if not golden.is_file():
        pytest.skip("golden template not present")
    work = tmp_path / "g.hwpx"
    shutil.copyfile(golden, work)
    res = apply_report_meta(work, {**META, "subtitle": "부제"}, use_profile=False)
    assert "co_researcher" in res.filled
    cover = _texts(work, 0)
    assert "정책연구 2099-01" in cover and ": 부제" in cover and "홍길동" in cover
    assert not [t for t in residual_tokens(work) if t.strip("{}") in ("title", "isbn")]


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
def test_normalize_meta_derivations() -> None:
    m = normalize_meta({
        "제목": "T",
        "연구책임": "홍길동  제주연구원 연구위원",
        "공동책임": "갑을병  제주연구원 연구위원",
        "공동연구": ["정무기  제주연구원 위촉연구원"],
        "발행일": "2099-8-31",
        "부제": "부제목",
        "report_number": 202601,
        "isbn": "",
    })
    assert m["title"] == "T"
    assert m["authors"] == "홍길동·갑을병"  # 앞표지: 연구책임 + 공동책임 only
    assert m["co_researcher"] == [
        "갑을병  제주연구원 연구위원", "정무기  제주연구원 위촉연구원"
    ]
    assert m["pub_date"] == "2099년 8월"
    assert m["subtitle"] == ": 부제목"
    assert m["report_number"] == "202601"
    assert "isbn" not in m


@pytest.mark.parametrize(
    ("end", "who"),
    [
        ("2026-08-31", "유 영 봉"),
        ("2026-10-05", "유 영 봉"),
        ("2026-10-06", "강 창 민"),  # 직무대행 starts that day (ji-facts)
        (date(2027, 1, 1), "강 창 민"),
        ("2025-01-01", None),  # before the table — no guess
        ("not a date", None),
        (None, None),
    ],
)
def test_publisher_for(end, who) -> None:
    assert publisher_for(end) == who


def test_explicit_publisher_wins_over_end_date() -> None:
    m = normalize_meta({"publisher": "직접", "end_date": "2026-10-06"})
    assert m["publisher"] == "직접"


def test_split_front_matter() -> None:
    md = (
        "﻿---\n"
        'title: "A\\nB"\n'
        "# a comment\n"
        "co_researchers:\n"
        "  - x 1\n"
        "  - 'y 2'\n"
        "note: |\n"
        "  l1\n"
        "  l2\n"
        "---\n"
        "# H\n"
    )
    meta, body = split_front_matter(md)
    assert meta == {"title": "A\nB", "co_researchers": ["x 1", "y 2"], "note": "l1\nl2"}
    assert body == "# H\n"
    assert split_front_matter("# no front matter\n---\n") == ({}, "# no front matter\n---\n")


@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_padded_slot_tokens_fill_and_expand(tmp_path: Path) -> None:
    """``{{ co_researcher }}`` / ``{{ title }}`` written with padding still fill (#69 review)."""
    tpl = tmp_path / "padded.hwpx"
    shutil.copyfile(STRATEGY, tpl)
    doc = HwpxDocument.open(str(tpl))
    for sec in doc.sections:
        for t in sec.element.iter(f"{_HP}t"):
            for slot in ("co_researcher", "title"):
                if t.text and "{{" + slot + "}}" in t.text:
                    t.text = t.text.replace("{{" + slot + "}}", "{{ " + slot + " }}")
        sec.mark_dirty()
    doc.save_to_path(str(tpl))
    apply_report_meta(tpl, META, use_profile=False)
    colophon = _texts(tpl, -1)
    left = " ".join(residual_tokens(tpl))  # only the never-written {{intro}}/{{body}}… remain
    assert "co_researcher" not in left and "title" not in left
    assert "공동연구   갑을병  제주연구원 연구위원" in colophon
    assert any("정무기" in t for t in colophon)
    assert "가상 연구의\n두 줄 제목" in _texts(tpl, 0)


@pytest.mark.skipif(not STRATEGY.is_file(), reason="baked house form not present")
def test_preview_text_reflects_filled_cover(tmp_path: Path) -> None:
    """Preview/PrvText.txt is regenerated after the cover fill (#69 review)."""
    _, out = _build(tmp_path, META)
    with zipfile.ZipFile(out) as z:
        prv = z.read("Preview/PrvText.txt").decode("utf-8")
    assert "{{" not in prv
    assert "홍길동" in prv
