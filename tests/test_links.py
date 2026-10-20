"""Markdown links ↔ Hangul hyperlink fields (issue #27)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.ops import fill_from_markdown
from hwp_agent.ops.extract import extract_markdown
from hwp_agent.ops.integrity import audit_package
from hwp_agent.ops.links import (
    DEFAULT_LINK_ALIGN_THRESHOLD,
    audit_links,
    command_for_url,
    link_pieces,
    rendered_text,
    should_left_align,
    url_from_command,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TPL = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
TOC_LINKS = REPO_ROOT / "tests" / "fixtures" / "sample_big_ref.hwpx"
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HH = "{http://www.hancom.co.kr/hwpml/2011/head}"


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("plain", [("plain", None)]),
        ("[제주연구원](https://www.jri.re.kr) 참고",
         [("제주연구원", "https://www.jri.re.kr"), (" 참고", None)]),
        ('[보고서](https://b.org/r.pdf "제목")', [("보고서", "https://b.org/r.pdf")]),
        ("[위키](https://a.org/(x)) 끝", [("위키", "https://a.org/(x)"), (" 끝", None)]),
        # Korean particle glued to the host / an ASCII path end is not URL
        ("https://www.law.go.kr에서 확인",
         [("https://www.law.go.kr", "https://www.law.go.kr"), ("에서 확인", None)]),
        ("https://x.kr/page에서", [("https://x.kr/page", "https://x.kr/page"), ("에서", None)]),
        # …but a Korean path segment is
        ("https://www.law.go.kr/법령/개인정보보호법 (접속)",
         [("https://www.law.go.kr/법령/개인정보보호법",
           "https://www.law.go.kr/법령/개인정보보호법"), (" (접속)", None)]),
        ("https://e.com/a?x=1&y=2.",
         [("https://e.com/a?x=1&y=2", "https://e.com/a?x=1&y=2"), (".", None)]),
        ("(https://a.org/x) 괄호",
         [("(", None), ("https://a.org/x", "https://a.org/x"), (") 괄호", None)]),
        ("「https://a.kr/b」", [("「", None), ("https://a.kr/b", "https://a.kr/b"), ("」", None)]),
        ("<https://auto.link/p>를", [("https://auto.link/p", "https://auto.link/p"), ("를", None)]),
        # not links: footnote refs, images, schemeless targets
        ("각주[^a] 이미지 ![x](https://i.png)", [("각주[^a] 이미지 ![x](https://i.png)", None)]),
        ("[상대](./a.md)", [("[상대](./a.md)", None)]),
        ("![x](<https://i.png>)", [("![x](<https://i.png>)", None)]),
        # a Korean query / fragment value is part of the URL (PR #28 review)
        ("https://e.com/search?q=제주 검색",
         [("https://e.com/search?q=제주", "https://e.com/search?q=제주"), (" 검색", None)]),
        ("https://e.com/#섹션", [("https://e.com/#섹션", "https://e.com/#섹션")]),
    ],
)
def test_link_pieces(text, expected) -> None:
    assert link_pieces(text) == expected


def test_rendered_text_drops_link_markup() -> None:
    assert rendered_text("[짧게](https://very.long/url/path) 끝") == "짧게 끝"


def test_command_round_trip() -> None:
    url = "http://h/p.do?a=b==;x\\y"
    cmd = command_for_url(url)
    assert cmd == "http\\://h/p.do?a=b==\\;x\\\\y;1;0;2;"
    assert url_from_command(cmd) == url
    # a real Hangul-saved URL link (JRI 그룹웨어 기안 서식)
    assert url_from_command(
        r"http\://210.127.210.237/docselect/DS010100P01.do\?schWscNo=Mj==;1;0;2;"
    ) == "http://210.127.210.237/docselect/DS010100P01.do?schWscNo=Mj=="
    assert url_from_command("?#251782870;0;1;0;") is None  # in-document (목차) link


# --------------------------------------------------------------------------- #
# write → hwpx
# --------------------------------------------------------------------------- #
MD = (
    "# 서론\n\n"
    "본문 [제주연구원](https://www.jri.re.kr) 과 https://example.com/a?x=1&y=2.[^src]\n\n"
    "출처 목록\n"
    "| 기관 | 출처 |\n|---|---|\n| OECD | https://www.oecd.org/ai |\n\n"
    "[^src]: 법제처, https://www.law.go.kr/법령/x (접속), [원문](https://c.org).\n"
)


def _sections(path: Path) -> list:
    with zipfile.ZipFile(path) as zf:
        return [
            etree.fromstring(zf.read(n)) for n in sorted(zf.namelist())
            if n.startswith("Contents/section")
        ]


def _links(path: Path) -> list[tuple[str, str]]:
    """(display text, Command) of every hyperlink field, in document order."""
    out = []
    for root in _sections(path):
        for fb in root.iter(f"{_HP}fieldBegin"):
            if fb.get("type") != "HYPERLINK":
                continue
            cmd = next(
                sp.text for sp in fb.iter(f"{_HP}stringParam") if sp.get("name") == "Command"
            )
            run = fb.getparent().getparent()
            text = "".join(t.text or "" for t in run.findall(f"{_HP}t"))
            out.append((text, cmd))
    return out


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_write_links_body_table_and_footnote(tmp_path: Path) -> None:
    out = tmp_path / "l.hwpx"
    res = fill_from_markdown(TPL, MD, output=out)
    links = _links(out)
    assert res.links == len(links) == 5
    assert ("제주연구원", "https\\://www.jri.re.kr;1;0;2;") in links
    assert ("https://example.com/a?x=1&y=2", "https\\://example.com/a?x=1&y=2;1;0;2;") in links
    assert ("https://www.oecd.org/ai", "https\\://www.oecd.org/ai;1;0;2;") in links  # cell
    assert ("원문", "https\\://c.org;1;0;2;") in links  # inside the footnote note
    # the footnote-note link lives inside the footnote
    fn_links = [
        fb for root in _sections(out) for fn in root.iter(f"{_HP}footNote")
        for fb in fn.iter(f"{_HP}fieldBegin")
    ]
    assert len(fn_links) == 2
    # every begin has its end, nothing left as bare URL text
    a = [audit_links(r) for r in _sections(out)]
    assert not any(x["unclosed"] or x["orphan_ends"] or x["bare_urls"] for x in a)
    assert not [i for i in audit_package(out) if i.kind == "field"]
    # Markdown markup is gone from the text
    xml = "".join(etree.tostring(r, encoding="unicode") for r in _sections(out))
    assert "](https" not in xml


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_write_extract_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "l.hwpx"
    fill_from_markdown(TPL, MD, output=out)
    md = extract_markdown(out)
    assert "[제주연구원](https://www.jri.re.kr)" in md
    assert "https://example.com/a?x=1&y=2" in md and "[https://" not in md
    assert "| https://www.oecd.org/ai |" in md
    assert "[원문](https://c.org)" in md  # footnote definition keeps its link


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_no_links_keeps_text_and_linkless_md_unchanged(tmp_path: Path) -> None:
    off = tmp_path / "off.hwpx"
    res = fill_from_markdown(TPL, MD, output=off, links=False)
    assert res.links == 0 and _links(off) == []
    bare = sum(len(audit_links(r)["bare_urls"]) for r in _sections(off))
    assert bare == 5
    # Markdown without links: identical section XML with links on or off
    md = "# 제목\n\n본문[^a] 문장\n\n[^a]: 링크 없는 각주\n"
    a, b = tmp_path / "a.hwpx", tmp_path / "b.hwpx"
    fill_from_markdown(TPL, md, output=a)
    fill_from_markdown(TPL, md, output=b, links=False)
    def norm(root) -> bytes:  # paragraph ids are random per run (python-hwpx)
        for p in root.iter(f"{_HP}p"):
            p.attrib.pop("id", None)
        return etree.tostring(root)

    assert [norm(r) for r in _sections(a)] == [norm(r) for r in _sections(b)]


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_blue_links_clone_char(tmp_path: Path) -> None:
    out = tmp_path / "blue.hwpx"
    fill_from_markdown(TPL, "본문 [a](https://a.org) [b](https://b.org)\n", output=out,
                       blue_links=True)
    chars = {
        fb.getparent().getparent().get("charPrIDRef")
        for r in _sections(out) for fb in r.iter(f"{_HP}fieldBegin")
    }
    assert len(chars) == 1  # both links share one cloned char
    with zipfile.ZipFile(out) as zf:
        header = etree.fromstring(zf.read("Contents/header.xml"))
    cp = header.find(f".//{_HH}charPr[@id='{chars.pop()}']")
    assert cp.get("textColor") == "#0000FF"
    assert cp.find(f"{_HH}underline").get("type") == "BOTTOM"
    assert not [i for i in audit_package(out) if i.kind in ("order", "count", "dangling")]


# --------------------------------------------------------------------------- #
# existing documents
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not TOC_LINKS.is_file(), reason="fixture missing")
def test_toc_links_are_not_urls_and_pairs_match() -> None:
    """Hangul's own 목차 links (in-document targets) stay text in extract and are
    well-formed field pairs for check."""
    assert not [i for i in audit_package(TOC_LINKS) if i.kind == "field"]
    md = extract_markdown(TOC_LINKS)
    assert "](" not in md


def test_audit_reports_broken_pair() -> None:
    xml = (
        f'<hs:sec xmlns:hs="x" xmlns:hp="{_HP[1:-1]}"><hp:p><hp:run>'
        '<hp:ctrl><hp:fieldBegin id="7" type="HYPERLINK"/></hp:ctrl><hp:t>a</hp:t>'
        "</hp:run></hp:p><hp:p><hp:run><hp:t>see https://x.org</hp:t>"
        '<hp:ctrl><hp:fieldEnd beginIDRef="9"/></hp:ctrl></hp:run></hp:p></hs:sec>'
    )
    a = audit_links(etree.fromstring(xml))
    assert a["unclosed"] == ["7"] and a["orphan_ends"] == ["9"]
    assert a["hyperlinks"] == 1


# --------------------------------------------------------------------------- #
# link alignment (issue #35)
# --------------------------------------------------------------------------- #
def test_should_left_align_boundary() -> None:
    assert DEFAULT_LINK_ALIGN_THRESHOLD == 30

    # 29 chars unbroken: False
    bare_29 = "https://example.com/123456789"
    assert len(bare_29) == 29
    assert not should_left_align(bare_29)

    # 30 chars unbroken: True
    bare_30 = "https://example.com/1234567890"
    assert len(bare_30) == 30
    assert should_left_align(bare_30)

    # Markdown link with 29-char unbroken label
    md_29 = "[12345678901234567890123456789](https://x.org)"
    assert not should_left_align(md_29)

    # Markdown link with 30-char unbroken label
    md_30 = "[123456789012345678901234567890](https://x.org)"
    assert should_left_align(md_30)

    # Short label with long URL
    short_label = "[짧은 링크](https://very-long-url-over-thirty-characters.example.com/path)"
    assert not should_left_align(short_label)

    # Long label but with spaces so each token is < 30
    spaces_label = "[짧은 단어가 여러 개 모여서 총 길이는 삼십 자가 넘지만 공백이 있는 링크](https://x.org)"
    assert not should_left_align(spaces_label)

    # Plain text without link
    assert not should_left_align(
        "일반 텍스트 문단입니다. 띄어쓰기없는아주긴글자도링크가아니면왼쪽정렬하지않음."
    )


def test_should_left_align_modes() -> None:
    long_url = "https://example.com/very/long/url/that/exceeds/thirty/characters"
    short_link = "[링크](https://x.org)"

    # keep mode: never left-aligns
    assert not should_left_align(long_url, mode="keep")
    assert not should_left_align(short_link, mode="keep")

    # left mode: left-aligns whenever a link is present
    assert should_left_align(long_url, mode="left")
    assert should_left_align(short_link, mode="left")
    assert not should_left_align("링크 없는 일반 문단", mode="left")

    # auto mode: checks length threshold
    assert should_left_align(long_url, mode="auto")
    assert not should_left_align(short_link, mode="auto")


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_link_align_in_document(tmp_path: Path) -> None:
    """Issue #35: long URLs trigger LEFT paraPr in body, table cell, and footnote;
    short links stay JUSTIFY; identical variants are reused; keep preserves JUSTIFY."""
    long_url = "https://example.com/a/very/long/url/path/that/exceeds/thirty/characters/easily"
    short_link = "[짧은 안내](https://example.com/short)"

    md = f"""# 1. 제목

첫 번째 본문 문단에는 아주 긴 {long_url} 이 들어갑니다.[^fn1]

두 번째 본문 문단에는 {short_link} 만 들어갑니다.

[표 1] 표 제목

| 항목 | 내용 |
| --- | --- |
| 표 셀 1 | 긴 링크 {long_url}/table 들어감 |
| 표 셀 2 | {short_link} 짧음 |

세 번째 본문 문단에도 다른 긴 {long_url}/another 가 들어갑니다.

[^fn1]: 각주 내용에 긴 링크 {long_url}/footnote 포함됨.
"""

    out_auto = tmp_path / "out_auto.hwpx"
    res_auto = fill_from_markdown(TPL, md, output=out_auto, link_align="auto")
    assert res_auto.placed > 0

    # 무결성 검사
    issues = audit_package(out_auto)
    assert not [i for i in issues if i.kind in ("order", "count", "dangling", "field")]

    with zipfile.ZipFile(out_auto) as zf:
        header_xml = etree.fromstring(zf.read("Contents/header.xml"))
        sec_xml = etree.fromstring(zf.read("Contents/section0.xml"))

    def get_align(para_pr_id: str) -> str:
        pp = header_xml.find(f".//{_HH}paraPr[@id='{para_pr_id}']")
        assert pp is not None, f"paraPr {para_pr_id} not found"
        al = pp.find(f"{_HH}align")
        return al.get("horizontal") if al is not None else "JUSTIFY"

    paras = sec_xml.findall(f".//{_HP}p")
    body_p_long1 = None
    body_p_short = None
    body_p_long2 = None
    cell_p_long = None
    cell_p_short = None
    fn_p_long = None

    for p in paras:
        txt = "".join(p.itertext())
        if "첫 번째 본문" in txt:
            body_p_long1 = p
        elif "두 번째 본문" in txt:
            body_p_short = p
        elif "세 번째 본문" in txt:
            body_p_long2 = p
        elif "긴 링크" in txt and "table" in txt:
            cell_p_long = p
        elif "짧음" in txt:
            cell_p_short = p
        if "각주 내용" in txt:
            fn_p_long = p

    assert body_p_long1 is not None
    assert body_p_short is not None
    assert body_p_long2 is not None
    assert cell_p_long is not None
    assert cell_p_short is not None
    assert fn_p_long is not None

    p1_pr = body_p_long1.get("paraPrIDRef")
    p2_pr = body_p_short.get("paraPrIDRef")
    p3_pr = body_p_long2.get("paraPrIDRef")
    cell_long_pr = cell_p_long.get("paraPrIDRef")
    cell_short_pr = cell_p_short.get("paraPrIDRef")
    fn_pr = fn_p_long.get("paraPrIDRef")

    assert get_align(p1_pr) == "LEFT"
    assert get_align(p2_pr) == "JUSTIFY"
    assert get_align(p3_pr) == "LEFT"
    # 재사용 검증: 동일한 BODY 원본에서 파생된 p1과 p3는 동일한 paraPrIDRef여야 함
    assert p1_pr == p3_pr
    assert get_align(cell_long_pr) == "LEFT"
    assert get_align(cell_short_pr) == "JUSTIFY"
    assert get_align(fn_pr) == "LEFT"

    # keep 모드 검증
    out_keep = tmp_path / "out_keep.hwpx"
    fill_from_markdown(TPL, md, output=out_keep, link_align="keep")
    with zipfile.ZipFile(out_keep) as zf:
        header_k = etree.fromstring(zf.read("Contents/header.xml"))
        sec_k = etree.fromstring(zf.read("Contents/section0.xml"))

    for p in sec_k.findall(f".//{_HP}p"):
        txt = "".join(p.itertext())
        if "첫 번째 본문" in txt or "긴 링크" in txt or "각주 내용" in txt:
            pr_id = p.get("paraPrIDRef")
            pp = header_k.find(f".//{_HH}paraPr[@id='{pr_id}']")
            al = pp.find(f"{_HH}align")
            assert al is None or al.get("horizontal") == "JUSTIFY"


def test_left_para_pr_skips_non_justify() -> None:
    from hwpx.document import HwpxDocument

    from hwp_agent.ops.author import _left_para_pr
    doc = HwpxDocument.open(str(TPL))
    header = doc._root._headers[0]
    pps = header._para_properties_element(create=False)

    left_el = None
    center_el = None
    justify_el = None
    for p in pps.findall(f"{_HH}paraPr"):
        al = p.find(f"{_HH}align")
        if al is not None:
            if al.get("horizontal") == "LEFT" and left_el is None:
                left_el = p
            elif al.get("horizontal") == "CENTER" and center_el is None:
                center_el = p
            elif al.get("horizontal") == "JUSTIFY" and justify_el is None:
                justify_el = p

    cache: dict[str, str] = {}
    if left_el is not None:
        lid = left_el.get("id")
        res = _left_para_pr(doc, lid, cache)
        assert res == lid  # 복제되지 않고 원래 id 유지

    if center_el is not None:
        cid = center_el.get("id")
        res = _left_para_pr(doc, cid, cache)
        assert res == cid  # CENTER는 건드리지 않음

    if justify_el is not None:
        jid = justify_el.get("id")
        res = _left_para_pr(doc, jid, cache)
        assert res != jid  # JUSTIFY는 복제됨
        # 재사용 확인
        assert _left_para_pr(doc, jid, cache) == res


@pytest.mark.skipif(not TPL.is_file(), reason="bundled default template missing")
def test_generic_table_cell_fallback_to_cp_para_pr(tmp_path: Path) -> None:
    """When a table zone has no paraPr (generic table), fall back to cell paragraph property."""
    long_url = "https://example.com/very/long/url/without/spaces/that/exceeds/thirty/chars"
    md = f"""# 제목

| A | B |
| --- | --- |
| 항목 | {long_url} |
"""
    out = tmp_path / "generic_tbl.hwpx"
    fill_from_markdown(TPL, md, output=out, link_align="auto")
    with zipfile.ZipFile(out) as zf:
        header_xml = etree.fromstring(zf.read("Contents/header.xml"))
        sec_xml = etree.fromstring(zf.read("Contents/section0.xml"))

    for p in sec_xml.findall(f".//{_HP}tc/{_HP}subList/{_HP}p"):
        txt = "".join(p.itertext())
        if "without/spaces" in txt:
            pr_id = p.get("paraPrIDRef")
            pp = header_xml.find(f".//{_HH}paraPr[@id='{pr_id}']")
            al = pp.find(f"{_HH}align")
            assert al is not None and al.get("horizontal") == "LEFT"

