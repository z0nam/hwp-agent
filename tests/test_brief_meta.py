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
    cfg = json.loads(OVERRIDE.read_text(encoding="utf-8"))["meta_slots"]
    assert {"cover", "colophon", "masterpage"} <= set(cfg)


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
    back = _text(out, "Contents/section4.xml")
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
