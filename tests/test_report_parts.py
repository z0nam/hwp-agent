"""#94 (+#106 #107 #108) — 요약·들어가며·참고문헌 land in their own golden sections."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.ops.report_parts import (
    _hp,
    _para_text,
    pop_summary_table,
    reference_headers,
    split_report_parts,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "examples" / "정책과제-template.hwpx"
DEFAULT_TPL = ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
BRIEF = ROOT / "examples" / "정책이슈브리프-template.hwpx"

#: the source form's placeholders, which must not print in a report
SAMPLES = ("장 제목", "절 제목", "핵심 주제", "세부 내용이나 근거", "~~~")

FULL = """---
title: "가상 보고서 제목"
---
# 요약

| 구분 | 주요내용 |
|---|---|
| 연구 목표 | 가상 목표 문장 |
| 연구 방법 | 가상 방법 하나<br>  - 가상 세부 방법<br>가상 방법 둘 |
| 정책 활용 분야 | 중앙부처 제안, 기타(가상 홍보) |
| 없는 행 | 무시됨 |

요약 가상 본문입니다.

# 들어가며

들어가며 가상 문단입니다.

# Ⅰ. 서론

본문 가상 문장입니다.

# 참고문헌

**해외 문헌**

Smith, J. (2020). A Title.

**국내 문헌**

홍길동 (2021). 가상 제목.
"""


def _paras(path: Path, idx: int) -> list:
    with zipfile.ZipFile(path) as z:
        return list(etree.fromstring(z.read(f"Contents/section{idx}.xml")).iter(_hp("p")))


def _texts(path: Path, idx: int) -> list[str]:
    return [_para_text(p) for p in _paras(path, idx)]


def _styles(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("Contents/header.xml"))
    return {
        s.get("name"): s.get("id")
        for s in root.iter("{http://www.hancom.co.kr/hwpml/2011/head}style")
    }


def test_split_takes_only_the_exact_bare_titles() -> None:
    md = (
        "# 요약\n\n요약문\n\n# 요약 및 결론\n\n결론\n\n```\n# 참고문헌\n```\n\n"
        "# 참고문헌\n\n문헌\n"
    )
    parts = dict(split_report_parts(md))
    assert parts["{{summary}}"].strip() == "요약문"
    assert "# 요약 및 결론" in parts["{{body}}"] and "# 참고문헌" in parts["{{body}}"]
    assert parts["{{references}}"].strip() == "문헌"
    assert split_report_parts("# Ⅰ. 서론\n\n본문\n") is None


def test_summary_table_and_reference_headers_are_read() -> None:
    rows, rest = pop_summary_table(FULL.split("# 요약")[1].split("# 들어가며")[0])
    assert rows[0] == ("연구 목표", "가상 목표 문장")
    assert "|" not in rest and "요약 가상 본문" in rest
    assert reference_headers("**해외 문헌**\n\n## 국내 문헌\n\n항목 **굵게** 끝\n") == [
        "해외 문헌", "국내 문헌",
    ]


@pytest.mark.skipif(not GOLDEN.is_file(), reason="golden template absent")
def test_golden_template_carries_no_sample_parts() -> None:
    for idx in (2, 3, 5):
        text = " ".join(_texts(GOLDEN, idx))
        assert not any(s in text for s in SAMPLES), (idx, text)
    assert {"참고문헌 분류", "참고문헌"} <= set(_styles(GOLDEN))


@pytest.mark.skipif(not GOLDEN.is_file(), reason="golden template absent")
def test_write_routes_parts_and_fills_the_form(tmp_path: Path, capsys) -> None:
    from hwp_agent.cli.main import main

    md = tmp_path / "r.md"
    md.write_text(FULL, encoding="utf-8")
    out = tmp_path / "r.hwpx"
    assert main(["write", str(md), "--template", str(GOLDEN), "-o", str(out)]) == 0
    assert "'없는 행' 행이 없습니다" in capsys.readouterr().err

    s2 = _texts(out, 2)
    assert "가상 목표 문장" in s2 and "요약 가상 본문입니다." in s2
    assert ["가상 방법 하나", "가상 세부 방법", "가상 방법 둘"] == [
        t for t in s2 if t.startswith("가상 방법") or t.startswith("가상 세부")
    ]
    assert "■ 중앙부처 제안" in s2 and "■ 기타(가상 홍보)" in s2
    assert "□ 국비 및 예산 확보" in s2
    assert "들어가며 가상 문단입니다." in _texts(out, 3)
    body = " ".join(_texts(out, 4))
    assert "본문 가상 문장" in body and "들어가며" not in body and "Smith" not in body

    ids = _styles(out)
    by_text = {_para_text(p): p for p in _paras(out, 5)}
    for head in ("해외 문헌", "국내 문헌"):
        assert by_text[head].get("styleIDRef") == ids["참고문헌 분류"]
    for entry in ("Smith, J. (2020). A Title.", "홍길동 (2021). 가상 제목."):
        assert by_text[entry].get("styleIDRef") == ids["참고문헌"]
        assert by_text[entry].get("paraPrIDRef") == "29"


@pytest.mark.skipif(not GOLDEN.is_file(), reason="golden template absent")
def test_write_without_the_parts_leaves_no_sample(tmp_path: Path) -> None:
    from hwp_agent.cli.main import main

    md = tmp_path / "b.md"
    md.write_text("# Ⅰ. 서론\n\n본문 가상 문장입니다.\n", encoding="utf-8")
    out = tmp_path / "b.hwpx"
    assert main(["write", str(md), "--template", str(GOLDEN), "-o", str(out)]) == 0
    for idx in (2, 3, 5):
        text = " ".join(_texts(out, idx))
        assert "{{" not in text
        assert not any(s in text for s in SAMPLES), (idx, text)
    assert "요약" in _texts(out, 2) and "참고문헌" in _texts(out, 5)


@pytest.mark.parametrize("template", [DEFAULT_TPL, BRIEF], ids=["default", "brief"])
def test_other_templates_keep_the_old_write(template: Path) -> None:
    from hwp_agent.cli.main import _report_parts

    if not template.is_file():
        pytest.skip("template absent")
    assert _report_parts(template, "# 요약\n\n요약문\n\n# Ⅰ. 서론\n\n본문\n") is None


def test_split_gives_each_part_the_footnotes_it_uses() -> None:
    md = "# Ⅰ. 서론\n\n본문[^a]\n\n# 참고문헌\n\n문헌\n\n[^a]: 각주 내용\n    이어지는 줄\n"
    parts = dict(split_report_parts(md))
    assert "[^a]: 각주 내용\n    이어지는 줄" in parts["{{body}}"]
    assert "[^a]:" not in parts["{{references}}"]


def test_summary_table_without_a_form_stays_a_table(tmp_path: Path) -> None:
    from hwp_agent.ops import fill_from_markdown

    tpl = tmp_path / "t.hwpx"
    out = tmp_path / "o.hwpx"
    tpl.write_bytes(DEFAULT_TPL.read_bytes())
    fill_from_markdown(tpl, "{{summary}}\n", output=tpl)  # a marker paragraph, no form
    with zipfile.ZipFile(tpl) as z:
        assert "{{summary}}" in z.read("Contents/section0.xml").decode()
    fill_from_markdown(
        tpl, "| 구분 | 주요내용 |\n|---|---|\n| 연구 목표 | 표로 남는 내용 |\n",
        output=out, marker_token="{{summary}}",
    )
    with zipfile.ZipFile(out) as z:
        xml = z.read("Contents/section0.xml").decode()
    assert "표로 남는 내용" in xml and "<hp:tbl" in xml
