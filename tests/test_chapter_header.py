"""Tests for hwp_agent.ops.chapter_header (per-chapter side wing, issue #34)."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import apply_chapter_headers, build_report
from hwp_agent.ops.integrity import audit_package

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_FORM = REPO_ROOT / "examples" / "house-forms" / "정책과제_서식.normalized.hwpx"
DEFAULT_TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
TITLES = ["Ⅰ. 세계경제 전망", "Ⅱ. 국내경제 동향", "Ⅲ. 제주경제 진단", "Ⅳ. 정책 시사점"]

pytestmark = pytest.mark.skipif(not POLICY_FORM.is_file(), reason="house form not present")


def _manifest(tmp_path: Path, **extra) -> Path:
    shutil.copy(POLICY_FORM, tmp_path / "form.hwpx")
    body = "\n\n".join(f"# {t}\n\n## 소절\n\n{t} 본문 문단입니다." for t in TITLES)
    (tmp_path / "body.md").write_text(body, encoding="utf-8")
    (tmp_path / "intro.md").write_text("들어가며 본문.\n", encoding="utf-8")
    (tmp_path / "refs.md").write_text("참고문헌 하나.\n", encoding="utf-8")
    m = tmp_path / "m.json"
    m.write_text(json.dumps({
        "template": "form.hwpx", "output": "out.hwpx",
        "parts": {"intro": "intro.md", "body": "body.md", "references": "refs.md"},
        **extra,
    }, ensure_ascii=False), encoding="utf-8")
    return m


def _sections(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        return {
            n: z.read(n).decode("utf-8") for n in z.namelist()
            if re.fullmatch(r"Contents/section\d+\.xml", n)
        }


def _headers(xml: str) -> list[str]:
    return re.findall(r"<hp:header\b.*?</hp:header>", xml, re.S)


def test_each_chapter_gets_its_own_odd_page_wing(tmp_path: Path) -> None:
    build_report(_manifest(tmp_path))
    out = tmp_path / "out.hwpx"
    res = apply_chapter_headers(out)

    assert res.titles == TITLES
    assert res.sections == ["Contents/section4.xml"]
    body = _sections(out)["Contents/section4.xml"]
    headers = _headers(body)
    assert len(headers) == len(TITLES)  # one per chapter
    for h, title in zip(headers, TITLES, strict=True):
        assert 'applyPageType="ODD"' in h
        assert 'textDirection="VERTICAL"' in h
        assert re.findall(r"<hp:t>([^<]+)</hp:t>", h) == [title]
    # each header sits in its chapter's HEADING_1 paragraph, just ahead of the title
    for title in TITLES:
        heading = re.search(
            r'<hp:p [^>]*styleIDRef="1"[^>]*>(<hp:run [^>]*><hp:ctrl><hp:header\b.*?'
            r"</hp:header></hp:ctrl></hp:run>)<hp:run [^>]*><hp:t>" + re.escape(title),
            body, re.S,
        )
        assert heading is not None, title
    assert audit_package(out) == []


def test_wing_text_uses_the_heading_char_pr_at_wing_size(tmp_path: Path) -> None:
    build_report(_manifest(tmp_path))
    out = tmp_path / "out.hwpx"
    apply_chapter_headers(out)
    with zipfile.ZipFile(out) as z:
        header = z.read("Contents/header.xml").decode()
    body = _sections(out)["Contents/section4.xml"]
    m = re.search(
        r'</hp:header></hp:ctrl></hp:run><hp:run charPrIDRef="(\d+)"><hp:t>'
        + re.escape(TITLES[0]), body,
    )
    heading_cp = m.group(1)
    wing_cp = re.search(
        r'textDirection="VERTICAL".*?<hp:run charPrIDRef="(\d+)">', _headers(body)[0], re.S
    ).group(1)

    def char_pr(i: str) -> str:
        return re.search(rf'<hh:charPr id="{i}"[^>]*>.*?</hh:charPr>', header, re.S).group(0)

    base, wing = char_pr(heading_cp), char_pr(wing_cp)
    assert 'height="800"' in wing
    # same font, colour and weight — only id and size differ
    norm = lambda s: re.sub(r' (id|height)="[^"]*"', "", s)  # noqa: E731
    assert norm(base) == norm(wing)


def test_sections_without_chapter_titles_are_untouched(tmp_path: Path) -> None:
    build_report(_manifest(tmp_path))
    out = tmp_path / "out.hwpx"
    before = _sections(out)
    apply_chapter_headers(out)
    after = _sections(out)
    # 표지·목차·요약·들어가며·판권: byte-identical
    for n in ("section0", "section1", "section2", "section3", "section6"):
        part = f"Contents/{n}.xml"
        assert after[part] == before[part], part
    # the section after the body only gets an empty odd header that stops the last
    # chapter's wing from running into it
    refs = _headers(after["Contents/section5.xml"])
    assert len(refs) == 1 and 'applyPageType="ODD"' in refs[0]
    assert "<hp:rect" not in refs[0] and not re.search(r"<hp:t>[^<]", refs[0])
    assert after["Contents/section5.xml"].replace(
        re.search(r"<hp:run [^>]*><hp:ctrl><hp:header\b.*?</hp:header></hp:ctrl></hp:run>",
                  after["Contents/section5.xml"], re.S).group(0), ""
    ) == before["Contents/section5.xml"]


def test_template_without_picture_wing_is_left_alone(tmp_path: Path) -> None:
    src = tmp_path / "in.hwpx"
    from hwp_agent.ops import fill_from_markdown

    fill_from_markdown(DEFAULT_TPL, "# 장 하나\n\n본문\n\n# 장 둘\n\n본문\n", output=src)
    dst = tmp_path / "out.hwpx"
    res = apply_chapter_headers(src, dst)
    assert res.count == 0
    assert dst.read_bytes() == src.read_bytes()


def test_build_cli_sets_wings_unless_disabled(tmp_path: Path) -> None:
    from hwp_agent.cli.main import main

    on = tmp_path / "on"
    off = tmp_path / "off"
    on.mkdir()
    off.mkdir()
    assert main(["build", str(_manifest(on))]) == 0
    assert len(_headers(_sections(on / "out.hwpx")["Contents/section4.xml"])) == len(TITLES)
    assert main(["build", str(_manifest(off, chapter_header=False))]) == 0
    assert not any(_headers(x) for x in _sections(off / "out.hwpx").values())
