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
    audit_links,
    command_for_url,
    link_pieces,
    rendered_text,
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
