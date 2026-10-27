"""목차 fill — 차례·표 차례·그림 차례 from the built document (issue #33).

The house forms ship a previous report's 차례 as sample text; ``build`` now rewrites
it from the built headings and captions, the 차례 wrapped in Hangul's
``TABLEOFCONTENTS`` field (markup measured on a Hangul-saved report, docs/toc.md).
Fixtures are the repo's house forms plus a few lines of synthetic Markdown.
"""

from __future__ import annotations

import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops import build_report, fill_toc
from hwp_agent.ops.integrity import audit_package
from hwp_agent.ops.links import audit_package_links
from hwp_agent.ops.toc import TOC_COMMAND, TOC_FIELD_ID

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = REPO_ROOT / "examples" / "house-forms" / "정책과제_서식.normalized.hwpx"
DEFAULT_TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

BODY = """# Ⅰ. 서론

## 1. 연구 배경

본문 문장임

가상 표 하나 {label:tbl_a}

| 구분 | 값 |
|---|---|
| 가 | 1 |

## 2. 연구 방법

![가상 그림 하나](fig.png)

# Ⅱ. 분석

## 1. 현황

가상 표 둘 {label:tbl_b}

| 구분 | 값 |
|---|---|
| 나 | 2 |
"""


def _png(path: Path) -> None:
    raw = b"".join(b"\x00" + b"\x80\x80\xff" * 8 for _ in range(6))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 6, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _build(tmp_path: Path, body: str = BODY, **manifest_extra) -> tuple[object, Path]:
    _png(tmp_path / "fig.png")
    (tmp_path / "body.md").write_text(body, encoding="utf-8")
    manifest = {"template": str(POLICY), "output": "out.hwpx", "parts": {"body": "body.md"},
                **manifest_extra}
    (tmp_path / "m.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    return build_report(tmp_path / "m.json"), tmp_path / "out.hwpx"


def _sections(path: Path) -> dict[str, etree._Element]:
    with zipfile.ZipFile(path) as z:
        return {n: etree.fromstring(z.read(n)) for n in z.namelist()
                if re.fullmatch(r"Contents/section\d+\.xml", n)}


def _texts(root) -> list[str]:
    return ["".join(t.text or "" for t in p.iter(f"{_HP}t")).strip()
            for p in root.findall(f"{_HP}p")]


def _commands(root, field_type: str) -> list[str]:
    return [
        sp.text for fb in root.iter(f"{_HP}fieldBegin") if fb.get("type") == field_type
        for sp in fb.iter(f"{_HP}stringParam") if sp.get("name") == "Command"
    ]


def test_build_rewrites_toc_from_headings_and_captions(tmp_path: Path) -> None:
    res, out = _build(tmp_path)
    assert res.toc == {"section": "Contents/section1.xml", "headings": 5, "tables": 2,
                       "figures": 1}
    secs = _sections(out)
    toc = secs["Contents/section1.xml"]
    texts = [t for t in _texts(toc) if t]
    # the form's sample report is gone
    assert not any("세계경제" in t or "GDP" in t or "제주경제" in t for t in texts)
    assert texts[:6] == ["요약","Ⅰ. 서론", "1. 연구 배경", "2. 연구 방법", "Ⅱ. 분석", "1. 현황"]
    # number-less part entries stay, their sample page numbers cleared
    assert "참고문헌" in texts and "부록" in texts
    tables = texts[texts.index("표 차례") + 1: texts.index("그림 차례")]
    assert len(tables) == 2 and "가상 표 하나" in tables[0] and "가상 표 둘" in tables[1]
    assert re.search(r"그림 .*1.*가상 그림 하나", texts[texts.index("그림 차례") + 1])


def test_toc_field_wraps_heading_entries_like_hangul(tmp_path: Path) -> None:
    _, out = _build(tmp_path)
    secs = _sections(out)
    toc = secs["Contents/section1.xml"]
    begins = [fb for fb in toc.iter(f"{_HP}fieldBegin") if fb.get("type") == "TABLEOFCONTENTS"]
    assert len(begins) == 1
    assert begins[0].get("fieldid") == TOC_FIELD_ID
    assert _commands(toc, "TABLEOFCONTENTS") == [TOC_COMMAND]
    ends = [fe for fe in toc.iter(f"{_HP}fieldEnd")
            if fe.get("beginIDRef") == begins[0].get("id")]
    assert len(ends) == 1 and ends[0].get("fieldid") == TOC_FIELD_ID
    # every field in the package is paired; integrity clean
    assert audit_package_links(out)["broken"] == []
    assert audit_package(out) == []


def test_entry_links_point_at_their_targets(tmp_path: Path) -> None:
    _, out = _build(tmp_path)
    secs = _sections(out)
    toc = secs.pop("Contents/section1.xml")
    para_ids = {p.get("id") for r in secs.values() for p in r.iter(f"{_HP}p")}
    tbl_ids = {t.get("id") for r in secs.values() for t in r.iter(f"{_HP}tbl")}
    pic_ids = {t.get("id") for r in secs.values() for t in r.iter(f"{_HP}pic")}
    links = [re.fullmatch(r"\?#(\d+);0;(\d);0;", c) for c in _commands(toc, "HYPERLINK")]
    assert all(links)
    kinds = [m.group(2) for m in links]
    assert kinds.count("1") == 5 and kinds.count("2") == 2 and kinds.count("3") == 1
    for m in links:
        pool = {"1": para_ids, "2": tbl_ids, "3": pic_ids}[m.group(2)]
        assert m.group(1) in pool
    # heading targets are unique, real ids
    outline = [m.group(1) for m in links if m.group(2) == "1"]
    assert len(set(outline)) == 5 and not {"0", "2147483648"} & set(outline)


def test_page_numbers_are_left_empty(tmp_path: Path) -> None:
    _, out = _build(tmp_path)
    toc = _sections(out)["Contents/section1.xml"]
    for p in toc.findall(f"{_HP}p"):
        for t in p.iter(f"{_HP}t"):
            for tab in t.iter(f"{_HP}tab"):
                if tab.get("type") == "2":
                    tail = (tab.tail or "").strip()
                    assert tail in ("", "ⅰ"), tail


def test_no_tables_drops_the_table_list_header(tmp_path: Path) -> None:
    res, out = _build(tmp_path, body="# Ⅰ. 서론\n\n## 1. 배경\n\n본문.\n")
    assert res.toc["tables"] == 0 and res.toc["figures"] == 0
    texts = _texts(_sections(out)["Contents/section1.xml"])
    assert "표 차례" not in texts and "그림 차례" not in texts


def test_toc_false_keeps_the_template(tmp_path: Path) -> None:
    res, out = _build(tmp_path, toc=False)
    assert res.toc is None
    texts = _texts(_sections(out)["Contents/section1.xml"])
    assert any("세계경제" in t for t in texts)


def test_cli_no_toc(tmp_path: Path, capsys) -> None:
    _build(tmp_path)  # writes manifest + inputs
    assert main(["build", str(tmp_path / "m.json"), "-o", str(tmp_path / "a.hwpx")]) == 0
    assert "목차: 차례 5 · 표 차례 2 · 그림 차례 1" in capsys.readouterr().out
    assert main(["build", str(tmp_path / "m.json"), "-o", str(tmp_path / "b.hwpx"),
                 "--no-toc"]) == 0
    assert "목차:" not in capsys.readouterr().out


def test_template_without_toc_section_is_untouched(tmp_path: Path) -> None:
    dst = tmp_path / "copy.hwpx"
    res = fill_toc(DEFAULT_TPL, dst)
    assert not res.filled
    assert dst.read_bytes() == DEFAULT_TPL.read_bytes()
