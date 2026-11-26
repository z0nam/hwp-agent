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

import pytest
from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops import build_report, fill_toc
from hwp_agent.ops.integrity import audit_package
from hwp_agent.ops.links import audit_package_links
from hwp_agent.ops.toc import (
    TOC_COMMAND,
    TOC_FIELD_ID,
    figure_toc_command,
    table_toc_command,
)

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

가상 표 셋

| 구분 | 값 |
|---|---|
| 다 | 3 |

## 2. 연구 방법

![가상 그림 하나](fig.png)

# Ⅱ. 분석

## 1. 현황

가상 표 둘 {label:tbl_b}

| 구분 | 값 |
|---|---|
| 나 | 2 |

[그림 Ⅱ-1] 가상 그림 둘

![왼쪽](fig.png) ![오른쪽](fig.png)
주) 좌: 가, 우: 나
"""

#: what the body captions say — the lists must match them exactly
TABLES = ["<표 Ⅰ-1> 가상 표 하나", "<표 Ⅰ-2> 가상 표 셋", "<표 Ⅱ-1> 가상 표 둘"]
FIGURES = ["[그림 Ⅰ-1] 가상 그림 하나", "[그림 Ⅱ-1] 가상 그림 둘"]
HEADINGS = ["Ⅰ. 서론", "1. 연구 배경", "2. 연구 방법", "Ⅱ. 분석", "1. 현황"]
BRIEF = REPO_ROOT / "examples" / "정책이슈브리프-template.hwpx"


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


def _lists(out: Path) -> tuple[list[str], list[str], list[str]]:
    texts = [t for t in _texts(_sections(out)["Contents/section1.xml"]) if t]
    tables = texts[texts.index("표 차례") + 1: texts.index("그림 차례")]
    figures = texts[texts.index("그림 차례") + 1:]
    return texts, tables, figures


def _toc_fields(root, make: int = 17) -> tuple[list, list]:
    """Begins / ends of the 차례 fields of one kind (ContentsMake 17 제목, 2 표, 4 그림)."""
    begins = [fb for fb in root.iter(f"{_HP}fieldBegin") if fb.get("type") == "TABLEOFCONTENTS"
              and f"ContentsMake:uint:{make} " in _commands(fb, "TABLEOFCONTENTS")[0]]
    ids = {fb.get("id") for fb in begins}
    ends = [fe for fe in root.iter(f"{_HP}fieldEnd") if fe.get("fieldid") == TOC_FIELD_ID
            and fe.get("beginIDRef") in ids]
    return begins, ends


def _body_captions(out: Path) -> list[str]:
    caps = []
    for name, root in _sections(out).items():
        if name == "Contents/section1.xml":
            continue
        for obj in root.iter(f"{_HP}tbl"):
            cap = obj.find(f"{_HP}caption")
            if obj.get("numberingType") in ("TABLE", "PICTURE") and cap is not None:
                caps.append(" ".join(t for t in _texts(cap.find(f"{_HP}subList")) if t))
    return caps


@pytest.mark.parametrize("literal", [False, True])
def test_build_rewrites_toc_from_headings_and_captions(tmp_path: Path, literal: bool) -> None:
    res, out = _build(tmp_path, literal_numbering=literal)
    assert res.toc == {"section": "Contents/section1.xml", "headings": 5, "tables": 3,
                       "figures": 2, "field": literal, "table_field": True,
                       "figure_field": True}
    texts, tables, figures = _lists(out)
    # the form's sample report is gone
    assert not any("세계경제" in t or "GDP" in t or "제주경제" in t for t in texts)
    # headings once each: the chapter wing (textbox / odd-page header) is not counted
    assert texts[:6] == ["요약", *HEADINGS]
    assert "참고문헌" in texts and "부록" in texts
    # table / figure lists say exactly what the body captions say
    assert tables == TABLES and figures == FIGURES
    assert sorted(_body_captions(out)) == sorted(TABLES + FIGURES)


def test_outline_headings_get_hangul_toc_field(tmp_path: Path) -> None:
    """--literal-numbering headings are outline paragraphs (#97): wrap them like Hangul."""
    _, out = _build(tmp_path, literal_numbering=True)
    toc = _sections(out)["Contents/section1.xml"]
    begins, ends = _toc_fields(toc)
    assert len(begins) == 1 and begins[0].get("fieldid") == TOC_FIELD_ID
    assert _commands(toc, "TABLEOFCONTENTS") == [
        TOC_COMMAND, table_toc_command(), figure_toc_command()]
    assert len(ends) == 1 and ends[0].get("beginIDRef") == begins[0].get("id")
    # every 차례 field end has its begin (no strays)
    assert len([fe for fe in toc.iter(f"{_HP}fieldEnd") if fe.get("fieldid") == TOC_FIELD_ID]) == 3
    assert audit_package_links(out)["broken"] == []
    assert audit_package(out) == []


def test_non_outline_headings_get_a_static_toc(tmp_path: Path) -> None:
    """Without outline headings a 차례 새로 고침 would find nothing — no field."""
    _, out = _build(tmp_path)
    toc = _sections(out)["Contents/section1.xml"]
    assert _toc_fields(toc) == ([], [])
    assert len(_toc_fields(toc, 2)[0]) == 1  # the 표 차례 needs no outline
    assert len(_toc_fields(toc, 4)[0]) == 1  # nor the 그림 차례 (#119, #128)
    assert len(_commands(toc, "HYPERLINK")) == 5 + 3 + 2
    assert audit_package_links(out)["broken"] == []
    assert audit_package(out) == []


@pytest.mark.parametrize("literal", [False, True])
def test_entry_links_point_at_their_targets(tmp_path: Path, literal: bool) -> None:
    _, out = _build(tmp_path, literal_numbering=literal)
    secs = _sections(out)
    toc = secs.pop("Contents/section1.xml")
    para_ids = {p.get("id") for r in secs.values() for p in r.iter(f"{_HP}p")}
    tbl_ids = {t.get("id") for r in secs.values() for t in r.iter(f"{_HP}tbl")}
    # a figure's entry links to its figure table, as Hangul's refresh does (#128)
    pic_ids = {t.get("id") for r in secs.values() for t in r.iter(f"{_HP}tbl")
               if t.get("numberingType") == "PICTURE"}
    links = [re.fullmatch(r"\?#(\d+);0;(\d);0;", c) for c in _commands(toc, "HYPERLINK")]
    assert all(links)
    kinds = [m.group(2) for m in links]
    assert kinds.count("1") == 5 and kinds.count("2") == 3 and kinds.count("3") == 2
    for m in links:
        pool = {"1": para_ids, "2": tbl_ids, "3": pic_ids}[m.group(2)]
        assert m.group(1) in pool
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
    toc = _sections(out)["Contents/section1.xml"]
    texts = _texts(toc)
    assert "표 차례" not in texts and "그림 차례" not in texts
    # their pages go too: nothing after the 차례 but no page break / blank spacers
    paras = toc.findall(f"{_HP}p")
    last = max(i for i, t in enumerate(texts) if t)
    assert not [p for p in paras[last + 1:] if p.get("pageBreak") == "1"]


def test_toc_false_keeps_the_template(tmp_path: Path) -> None:
    res, out = _build(tmp_path, toc=False)
    assert res.toc is None
    texts = _texts(_sections(out)["Contents/section1.xml"])
    assert any("세계경제" in t for t in texts)


def test_cli_no_toc(tmp_path: Path, capsys) -> None:
    _build(tmp_path)  # writes manifest + inputs
    assert main(["build", str(tmp_path / "m.json"), "-o", str(tmp_path / "a.hwpx")]) == 0
    assert "목차: 차례 5 · 표 차례 3 · 그림 차례 2" in capsys.readouterr().out
    assert main(["build", str(tmp_path / "m.json"), "-o", str(tmp_path / "b.hwpx"),
                 "--no-toc"]) == 0
    assert "목차:" not in capsys.readouterr().out


@pytest.mark.parametrize("literal", [False, True])
def test_fill_twice_is_stable(tmp_path: Path, literal: bool) -> None:
    _, out = _build(tmp_path, literal_numbering=literal)
    again = tmp_path / "again.hwpx"
    res = fill_toc(out, again)
    assert len(res.headings) == 5 and res.in_field == literal
    toc = _sections(again)["Contents/section1.xml"]
    begins, ends = _toc_fields(toc)
    assert (len(begins), len(ends)) == ((1, 1) if literal else (0, 0))
    assert tuple(map(len, _toc_fields(toc, 2))) == (1, 1)
    assert tuple(map(len, _toc_fields(toc, 4))) == (1, 1)
    assert len([fe for fe in toc.iter(f"{_HP}fieldEnd")
                if fe.get("fieldid") == TOC_FIELD_ID]) == (3 if literal else 2)
    assert _lists(again) == _lists(out)
    assert audit_package_links(again)["broken"] == []
    assert audit_package(again) == []


@pytest.mark.parametrize("tpl", [DEFAULT_TPL, BRIEF], ids=["default", "brief"])
def test_template_without_toc_section_is_untouched(tmp_path: Path, tpl: Path) -> None:
    """The brief fills its own 차례 slots ({{toc_1}}…) — fill_toc must not touch it."""
    dst = tmp_path / "copy.hwpx"
    res = fill_toc(tpl, dst)
    assert not res.filled
    assert dst.read_bytes() == tpl.read_bytes()


def test_built_brief_keeps_its_own_toc(tmp_path: Path) -> None:
    """A built brief (차례 filled from its {{toc_n}} slots) is not a 목차 section."""
    res, out = _build(tmp_path, template=str(BRIEF))
    assert res.toc is None
    again = tmp_path / "again.hwpx"
    assert not fill_toc(out, again).filled
    assert again.read_bytes() == out.read_bytes()


def test_lists_leave_no_page_break_inside(tmp_path: Path) -> None:
    """The form's sample 표 차례 runs over two pages; its page break must not stay
    between the generated entries (it made an empty page)."""
    _, out = _build(tmp_path)
    paras = _sections(out)["Contents/section1.xml"].findall(f"{_HP}p")
    texts = ["".join(t.text or "" for t in p.iter(f"{_HP}t")).strip() for p in paras]
    first = texts.index(TABLES[0])
    last = texts.index(TABLES[-1])
    breaks = [i for i, p in enumerate(paras) if p.get("pageBreak") == "1"]
    assert not [i for i in breaks if first < i <= last]
    # exactly one break between the table list and the figure list
    fig = texts.index(FIGURES[0])
    assert len([i for i in breaks if last < i < fig]) == 1


@pytest.mark.parametrize("literal", [False, True])
def test_refill_with_unnumbered_chapter(tmp_path: Path, literal: bool) -> None:
    """An unnumbered chapter title has no visible level; a refill must still find
    and replace its entry (it used to raise on an empty max())."""
    body = "# 서론\n\n## 1. 배경\n\n본문.\n\n# Ⅱ. 분석\n\n## 1. 현황\n\n본문.\n"
    res, out = _build(tmp_path, body=body, literal_numbering=literal)
    assert res.toc["headings"] == 4
    again = tmp_path / "again.hwpx"
    assert len(fill_toc(out, again).headings) == 4
    texts, _, _ = _lists(again) if "표 차례" in _texts(
        _sections(again)["Contents/section1.xml"]) else (
        [t for t in _texts(_sections(again)["Contents/section1.xml"]) if t], [], [])
    assert texts[:5] == ["요약", "서론", "1. 배경", "Ⅱ. 분석", "1. 현황"]
    assert texts.count("서론") == 1
    assert audit_package_links(again)["broken"] == []


_HH = "{http://www.hancom.co.kr/hwpml/2011/head}"


def _styles(path: Path) -> list[etree._Element]:
    with zipfile.ZipFile(path) as z:
        head = etree.fromstring(z.read("Contents/header.xml"))
    return head.find(f".//{_HH}styles").findall(f"{_HH}style")


def test_field_defines_hangul_toc_styles(tmp_path: Path) -> None:
    """A 차례 새로 고침 formats entries with Hangul's 차례 styles (by name) — defined
    with the form's look, the refresh keeps it."""
    _, out = _build(tmp_path, literal_numbering=True)
    styles = _styles(out)
    by_name = {s.get("name"): s for s in styles}
    for name in ("차례 제목", "차례 1", "차례 2", "차례 3"):
        assert name in by_name, name
    with zipfile.ZipFile(POLICY) as z:
        n_before = len(etree.fromstring(z.read("Contents/header.xml"))
                       .find(f".//{_HH}styles").findall(f"{_HH}style"))
    # appended at the end, ids continuing the list
    assert [int(s.get("id")) for s in styles] == list(range(len(styles)))
    assert len(styles) == n_before + 4
    # entries carry them: chapter → 차례 1, section → 차례 2, look = the form's sample
    toc = _sections(out)["Contents/section1.xml"]
    entries = [p for p in toc.findall(f"{_HP}p")
               if any(";0;1;0;" in c for c in _commands(p, "HYPERLINK"))]
    sid = {by_name[n].get("id"): n for n in ("차례 1", "차례 2")}
    assert [sid[p.get("styleIDRef")] for p in entries] == [
        "차례 1", "차례 2", "차례 2", "차례 1", "차례 2"]
    for p in entries:
        st = by_name[sid[p.get("styleIDRef")]]
        assert st.get("paraPrIDRef") == p.get("paraPrIDRef")
    assert audit_package(out) == []


def test_refill_reuses_toc_styles(tmp_path: Path) -> None:
    _, out = _build(tmp_path, literal_numbering=True)
    again = tmp_path / "again.hwpx"
    fill_toc(out, again)
    assert len(_styles(again)) == len(_styles(out))
    assert audit_package(again) == []


def test_table_list_is_a_toc_field(tmp_path: Path) -> None:
    """표 차례 = a Hangul 차례 field for tables only (ContentsMake 2), so a refresh fills
    its page numbers; the field ends right after the last entry."""
    _, out = _build(tmp_path)
    toc = _sections(out)["Contents/section1.xml"]
    begins, ends = _toc_fields(toc, 2)
    assert _commands(begins[0], "TABLEOFCONTENTS") == [table_toc_command()]
    paras = toc.findall(f"{_HP}p")
    texts = ["".join(t.text or "" for t in p.iter(f"{_HP}t")).strip() for p in paras]
    first, last = texts.index(TABLES[0]), texts.index(TABLES[-1])
    assert list(paras[first].iter(f"{_HP}fieldBegin"))[0] is begins[0]
    assert list(paras[last + 1].iter(f"{_HP}fieldEnd"))[0] is ends[0]
    # its entries take the 차례 1 style on refresh: the styles are there with a static 차례 too
    assert "차례 1" in {s.get("name") for s in _styles(out)}


def test_cli_build_keeps_decorations_out_of_lists(tmp_path: Path) -> None:
    """#109: only captioned tables / figures keep a numbering type in a build — a
    Hangul 표·그림 차례 would otherwise collect cover pictures and rule lines."""
    _build(tmp_path, literal_numbering=True)  # writes manifest + inputs
    out = tmp_path / "cli.hwpx"
    assert main(["build", str(tmp_path / "m.json"), "-o", str(out)]) == 0
    numbered = []
    with zipfile.ZipFile(out) as z:
        for name in z.namelist():
            if re.fullmatch(r"Contents/(section|masterpage)\d+\.xml", name):
                for el in etree.fromstring(z.read(name)).iter():
                    if isinstance(el.tag, str) and el.get("numberingType") not in (None, "NONE"):
                        numbered.append((el.get("numberingType"),
                                         el.find(f"{_HP}caption") is not None))
    assert sorted(numbered) == [("PICTURE", True)] * 2 + [("TABLE", True)] * 3


GOLDEN = REPO_ROOT / "examples" / "정책과제-template.hwpx"


@pytest.mark.parametrize("literal", [False, True])
def test_golden_template_toc(tmp_path: Path, literal: bool) -> None:
    """The golden template carries a neutral 목차 sample + Hangul's 차례 styles
    (scripts/add-golden-toc.py): build fills it like the house form."""
    res, out = _build(tmp_path, template=str(GOLDEN), literal_numbering=literal)
    assert res.toc == {"section": "Contents/section1.xml", "headings": 5, "tables": 3,
                       "figures": 2, "field": literal, "table_field": True,
                       "figure_field": True}
    texts, tables, figures = _lists(out)
    assert texts[:6] == ["요약", *HEADINGS]
    assert tables == TABLES and figures == FIGURES
    assert not any("장 제목" in t or "표 제목" in t or "그림 제목" in t for t in texts)
    toc = _sections(out)["Contents/section1.xml"]
    assert len(_toc_fields(toc)[0]) == (1 if literal else 0)
    assert len(_toc_fields(toc, 2)[0]) == 1
    # the template's own 차례 styles are reused, none added
    with zipfile.ZipFile(GOLDEN) as z:
        n_tpl = len(etree.fromstring(z.read("Contents/header.xml"))
                    .find(f".//{_HH}styles").findall(f"{_HH}style"))
    assert len(_styles(out)) == n_tpl
    assert audit_package_links(out)["broken"] == []
    assert audit_package(out) == []


def test_golden_template_has_no_sample_report_text() -> None:
    """Its 목차 sample is neutral — no text of the form's example report."""
    with zipfile.ZipFile(GOLDEN) as z:
        root = etree.fromstring(z.read("Contents/section1.xml"))
    words = {(t.text or "").strip() for t in root.iter(f"{_HP}t")} - {""}
    assert words == {"요약", "Ⅰ. 장 제목", "1. 절 제목", "1) 항 제목", "Ⅱ. 장 제목",
                     "참고문헌", "부록", "표 차례", "<표 Ⅰ-1> 표 제목", "<표 Ⅰ-2> 표 제목",
                     "그림 차례", "[그림 Ⅰ-1] 그림 제목", "[그림 Ⅰ-2] 그림 제목"}
