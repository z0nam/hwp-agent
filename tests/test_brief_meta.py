"""#91 stage 1 — 정책이슈브리프 표지·뒷표지·바탕쪽 메타 슬롯 (가상 원고)."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import build_report

BRIEF = Path(__file__).resolve().parents[1] / "examples" / "house-forms" / "정책이슈브리프.normalized.hwpx"
OVERRIDE = Path(__file__).resolve().parents[1] / "overrides" / "정책이슈브리프.hwp.json"

pytestmark = pytest.mark.skipif(not BRIEF.is_file(), reason="brief template absent")

FRONT = """---
제목: 가상 정책 이슈 제목입니다 // 두 번째 줄 부제
호수: "123"
저자: 홍길동 제주연구원 연구위원 ; 김철수 제주연구원 부연구위원
머리제목: 가상 머리 제목
발행인: 가 상 인
장시작: 1
---
# Ⅰ. 들어가며

가상 본문입니다
"""


def _text(path: Path, part: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read(part).decode()
    return re.sub(r"<[^>]+>", "|", xml)


def test_override_declares_meta_slots() -> None:
    data = json.loads(OVERRIDE.read_text(encoding="utf-8"))
    assert {"cover", "colophon", "masterpage"} <= set(data["meta_slots"])
    assert data["body"]["section"] == 1


def test_front_matter_fills_cover_back_cover_and_running_title(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text(FRONT, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(BRIEF), "output": "out.hwpx", "parts": {"body": "b.md"}}),
        encoding="utf-8",
    )
    build_report(manifest)
    out = tmp_path / "out.hwpx"
    cover = _text(out, "Contents/section0.xml")
    assert "VOL." in cover and "123" in cover
    # 서식은 제목 줄마다 문단 — 첫째·둘째 줄이 각각 자기 문단에
    assert "가상 정책 이슈 제목입니다" in cover and "두 번째 줄 부제" in cover
    assert "가상 정책 이슈 제목입니다 두 번째" not in cover  # two paragraphs, not one line
    assert "홍길동 제주연구원 연구위원" in cover and "김철수 제주연구원 부연구위원" in cover
    assert "{{" not in cover
    assert "가상 본문입니다" in _text(out, "Contents/section1.xml")  # 본문 구역
    back = _text(out, "Contents/section4.xml")
    assert "가상 본문입니다" not in back and "들어가며" not in back  # 뒷표지 침범 없음 (#105 한글 확인)
    assert "vol." in back and "123" in back and "가 상 인" in back and "{{" not in back
    with zipfile.ZipFile(out) as z:
        masters = "".join(z.read(n).decode() for n in z.namelist() if "masterpage" in n)
    assert "가상 머리 제목" in masters and "{{running_title}}" not in masters


def test_running_title_defaults_to_title(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text(FRONT.replace("머리제목: 가상 머리 제목\n", ""), encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(BRIEF), "output": "out.hwpx", "parts": {"body": "b.md"}}),
        encoding="utf-8",
    )
    build_report(manifest)
    with zipfile.ZipFile(tmp_path / "out.hwpx") as z:
        masters = "".join(z.read(n).decode() for n in z.namelist() if "masterpage" in n)
    assert "가상 정책 이슈 제목입니다 두 번째 줄 부제" in masters


def test_committed_template_title_slots_sit_on_the_cover_title_lines() -> None:
    """Review on #105: index 1 must be the 2nd cover-title paragraph, not the 인사말 one."""
    from lxml import etree

    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    with zipfile.ZipFile(BRIEF) as z:
        root = etree.fromstring(z.read("Contents/section0.xml"))
    texts = [
        "".join(t.text or "" for r in p.findall(hp + "run") for t in r.findall(hp + "t"))
        for p in root.iter(hp + "p")
    ]
    assert texts.count("{{title_1}}") == 1 and texts.count("{{title_2}}") == 1
    assert texts.count("○○○○○○○○○○") == 1  # only the 인사말 heading keeps its example
    i1, i2 = texts.index("{{title_1}}"), texts.index("{{title_2}}")
    assert i2 == i1 + 1  # consecutive: the two cover-title lines
    assert "제주연구원은 지역사회가 안고 있는 " not in texts[i2]  # not in the greeting


def test_missing_title_is_reported_for_a_brief(tmp_path: Path) -> None:
    """Review on #105: no title → both cover-title slots blank, so `title` must be missing."""
    (tmp_path / "b.md").write_text(
        FRONT.replace("제목: 가상 정책 이슈 제목입니다 // 두 번째 줄 부제\n", ""), encoding="utf-8"
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(BRIEF), "output": "out.hwpx", "parts": {"body": "b.md"}}),
        encoding="utf-8",
    )
    result = build_report(manifest)
    assert any("필수 항목" in w and "title" in w for w in result.warnings), result.warnings


def test_long_cover_title_shrinks_to_fit_its_box(tmp_path: Path) -> None:
    """한글 확인(#105): 긴 첫 줄이 상자 폭에서 넘어 '효과' 한 단어가 둘째 줄로 떨어짐."""
    long_front = FRONT.replace(
        "제목: 가상 정책 이슈 제목입니다 // 두 번째 줄 부제",
        "제목: 대형 스포츠 행사의 지역경제 파급 효과 // 가상 사례를 중심으로",
    )
    (tmp_path / "b.md").write_text(long_front, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(BRIEF), "output": "out.hwpx", "parts": {"body": "b.md"}}),
        encoding="utf-8",
    )
    build_report(manifest)
    out = tmp_path / "out.hwpx"
    with zipfile.ZipFile(out) as z:
        sec = z.read("Contents/section0.xml").decode()
        header = z.read("Contents/header.xml").decode()
    m = re.search(r'<hp:run charPrIDRef="(\d+)"><hp:t>대형 스포츠', sec)
    assert m, "title run not found"
    height = int(re.search(rf'<hh:charPr id="{m.group(1)}" height="(\d+)"', header).group(1))
    assert height < 2700  # shrunk from the template's 27 pt
    assert height >= int(2700 * 0.55)
    # the short title keeps the template size
    (tmp_path / "b2.md").write_text(FRONT, encoding="utf-8")
    manifest.write_text(
        json.dumps({"template": str(BRIEF), "output": "out2.hwpx", "parts": {"body": "b2.md"}}),
        encoding="utf-8",
    )
    build_report(manifest)
    with zipfile.ZipFile(tmp_path / "out2.hwpx") as z:
        sec2 = z.read("Contents/section0.xml").decode()
    assert re.search(r'<hp:run charPrIDRef="17"><hp:t>가상 정책 이슈 제목입니다', sec2)


def test_one_cell_table_becomes_a_summary_box(tmp_path: Path) -> None:
    """#91: a 1×1 Markdown table is the 요약 상자 — framed, character-anchored, un-numbered."""
    box = "| 가상 요약 문장입니다. 둘째 문장도 가상입니다. |\n|---|\n"
    (tmp_path / "b.md").write_text(
        FRONT.replace("가상 본문입니다", box + "\n가상 본문입니다"), encoding="utf-8"
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(BRIEF), "output": "out.hwpx", "parts": {"body": "b.md"}}),
        encoding="utf-8",
    )
    build_report(manifest)
    with zipfile.ZipFile(tmp_path / "out.hwpx") as z:
        sec = z.read("Contents/section1.xml").decode()
        header = z.read("Contents/header.xml").decode()
    tbl = re.search(r"<hp:tbl\b.*?</hp:tbl>", sec, re.S).group(0)
    assert 'numberingType="NONE"' in tbl  # uncaptioned → out of 표 차례 (#101)
    assert 'rowCnt="1"' in tbl and 'colCnt="1"' in tbl
    assert '<hp:pos treatAsChar="1"' in tbl
    assert 'vertAlign="CENTER"' in tbl
    assert re.search(r'<hp:inMargin left="510" right="510" top="141" bottom="141"', tbl)
    assert "가상 요약 문장입니다" in re.sub(r"<[^>]+>", "", tbl)
    bf = re.search(r'<hp:tc [^>]*borderFillIDRef="(\d+)"', tbl).group(1)
    fill = re.search(rf'<hh:borderFill id="{bf}".*?</hh:borderFill>', header, re.S).group(0)
    assert len(re.findall(r'(?:left|right|top|bottom)Border type="SOLID" width="0.12 mm" color="#000000"', fill)) == 4
    assert "가상 본문입니다" in sec  # the body text after the box is still there


def test_summary_box_takes_no_table_number() -> None:
    """Review on #105: a summary box is unnumbered, so the first real table is 표 1."""
    from hwp_agent.ops.author import TableBlock, _resolve_cross_refs, parse_markdown

    md = (
        "| 가상 요약 |\n|---|\n\n"
        "표 제목 {label:t1}\n\n| 가 | 나 |\n|---|---|\n| 1 | 2 |\n\n"
        "본문 {ref:t1} 참조\n"
    )
    blocks = parse_markdown(md)
    _resolve_cross_refs(blocks, "Ⅰ", [])
    assert any("표 Ⅰ-1 참조" in getattr(b, "text", "") for b in blocks), [
        getattr(b, "text", None) for b in blocks
    ]


def test_captioned_or_sourced_one_cell_table_is_not_a_summary_box() -> None:
    """Review on #105: caption / 출처 of a one-cell table must not be dropped."""
    from hwp_agent.ops.author import _is_summary_box, parse_markdown

    plain, captioned, sourced = (
        next(b for b in parse_markdown(m) if hasattr(b, "rows"))
        for m in (
            "| 요약 |\n|---|\n",
            "표 제목\n\n| 요약 |\n|---|\n",
            "| 요약 |\n|---|\n출처) 가상 자료\n",
        )
    )
    assert _is_summary_box(plain)
    assert not _is_summary_box(captioned) and not _is_summary_box(sourced)
