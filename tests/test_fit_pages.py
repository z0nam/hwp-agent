"""Page filling — `hwp-agent fit pages` (issue #40).

hwp2pdf is replaced by :class:`conftest.FakeHwp2Pdf` whose HWPX "re-save" runs
:func:`fake_hangul_layout`: a tiny paginator that reads the document's own
paraPr spacing and line spacing, so a tightened page really does take the chunk
back and the fitter's verification sees it.
"""

from __future__ import annotations

import json
import math
import re
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops.fit_pages import _clone_para_pr, fit_pages

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
SAMPLE = Path(__file__).parent / "data" / "tail_fit_sample.hwpx"

BODY = 20000  # usable page height of the test documents
LINE = 1000  # every line's height in the fake layout
CHARS = 10  # characters per line in the fake layout
BODY_PP = "90"  # paraPr: spacing 500/500, line spacing 160%
HEAD_PP = "91"  # paraPr: heading-like, keep with next


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _para_pr(pid: str, prev: int, nxt: int, percent: int, keep: bool = False) -> str:
    margin = (
        f'<hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="0" unit="HWPUNIT"/>'
        f'<hc:right value="0" unit="HWPUNIT"/><hc:prev value="{{p}}" unit="HWPUNIT"/>'
        f'<hc:next value="{{n}}" unit="HWPUNIT"/></hh:margin>'
        f'<hh:lineSpacing type="PERCENT" value="{percent}" unit="HWPUNIT"/>'
    )
    return (
        f'<hh:paraPr xmlns:hh="{HH}" xmlns:hc="{HC}" xmlns:hp="{HP}" id="{pid}" '
        'tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" '
        'suppressLineNumbers="0" checked="0"><hh:align horizontal="JUSTIFY" vertical="BASELINE"/>'
        '<hh:heading type="NONE" idRef="0" level="0"/>'
        '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="BREAK_WORD" '
        f'widowOrphan="0" keepWithNext="{int(keep)}" keepLines="0" pageBreakBefore="0" '
        'lineWrap="BREAK"/><hp:switch><hp:case hp:required-namespace='
        '"http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
        + margin.format(p=prev, n=nxt)
        + "</hp:case><hp:default>"
        + margin.format(p=prev * 2, n=nxt * 2)
        + "</hp:default></hp:switch></hh:paraPr>"
    )


def make_doc(path: Path, paras: list[tuple]) -> Path:
    """A one-section HWPX whose body is BODY high.

    Each item is ``(text, pageBreak)``, ``(text, pageBreak, paraPr)`` or
    ``("TABLE", height)`` for a treat-as-char table of that height.
    """
    with zipfile.ZipFile(SAMPLE) as z:
        parts = {i: z.read(i.filename) for i in z.infolist()}
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info, blob in parts.items():
            if info.filename == "Contents/header.xml":
                root = etree.fromstring(blob)
                props = root.find(".//" + _q(HH, "paraProperties"))
                props.append(etree.fromstring(_para_pr(BODY_PP, 500, 500, 160)))
                props.append(etree.fromstring(_para_pr(HEAD_PP, 800, 400, 160, keep=True)))
                props.set("itemCnt", str(len(props)))
                blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            elif info.filename == "Contents/section0.xml":
                root = etree.fromstring(blob)
                ps = root.findall(_q(HP, "p"))
                first = ps[0]
                for p in ps[1:]:
                    root.remove(p)
                for run in first.findall(_q(HP, "run")):
                    for child in list(run):
                        if child.tag != _q(HP, "secPr") and child.tag != _q(HP, "ctrl"):
                            run.remove(child)
                lsa = first.find(_q(HP, "linesegarray"))
                if lsa is not None:
                    first.remove(lsa)
                first.set("paraPrIDRef", BODY_PP)
                m = root.find(".//" + _q(HP, "pagePr")).find(_q(HP, "margin"))
                for k in ("header", "footer", "top", "bottom"):
                    m.set(k, "0")
                root.find(".//" + _q(HP, "pagePr")).set("height", str(BODY))
                for item in paras:
                    p = etree.SubElement(root, _q(HP, "p"))
                    p.set("paraPrIDRef", item[2] if len(item) > 2 else BODY_PP)
                    p.set("styleIDRef", "0")
                    p.set("pageBreak", "1" if item[0] != "TABLE" and item[1] else "0")
                    run = etree.SubElement(p, _q(HP, "run"), charPrIDRef="0")
                    if item[0] == "TABLE":
                        etree.SubElement(run, _q(HP, "tbl"), testh=str(item[1]))
                    else:
                        etree.SubElement(run, _q(HP, "t")).text = item[0]
                blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            dst.writestr(info, blob)
    path.write_bytes(out.getvalue())
    return path


def fake_hangul_layout(data: bytes, laid_out_pages: int | None = None,
                       ignore_cuts: dict | None = None) -> bytes:
    """Fake Hangul re-save: paginate with the document's own spacing.

    ``laid_out_pages`` mimics #65 (only the first pages saved with a layout);
    ``ignore_cuts`` lays out with these fixed paraPr values instead, so a
    tightened page does not change (to exercise the revert path).
    """
    src = zipfile.ZipFile(BytesIO(data))
    header = etree.fromstring(src.read("Contents/header.xml"))
    props = {}
    for pp in header.iter(_q(HH, "paraPr")):
        m = pp.find(".//" + _q(HH, "margin"))
        prev = int(m.find(_q(HC, "prev")).get("value")) if m is not None else 0
        nxt = int(m.find(_q(HC, "next")).get("value")) if m is not None else 0
        ls = pp.find(".//" + _q(HH, "lineSpacing"))
        props[pp.get("id")] = (prev, nxt, int(ls.get("value")) if ls is not None else 100)
    if ignore_cuts:
        props.update(ignore_cuts)
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            blob = src.read(info.filename)
            if re.search(r"Contents/section\d+\.xml$", info.filename):
                root = etree.fromstring(blob)
                page, y = 1, 0
                for i, p in enumerate(root.findall(_q(HP, "p"))):
                    lsa = p.find(_q(HP, "linesegarray"))
                    if lsa is not None:
                        p.remove(lsa)
                    sb, sa, pct = props[p.get("paraPrIDRef")]
                    tbl = p.find(f"{_q(HP, 'run')}/{_q(HP, 'tbl')}")
                    if tbl is not None:
                        heights = [int(tbl.get("testh"))]
                    else:
                        text = "".join(t.text or "" for t in p.iter(_q(HP, "t")))
                        heights = [LINE] * max(1, math.ceil(len(text) / CHARS))
                    if i and p.get("pageBreak") == "1":
                        page, y = page + 1, 0
                    segs = []
                    y += sb
                    for k, h in enumerate(heights):
                        if y + h > BODY and (segs or y > sb):
                            page, y = page + 1, (sb if k == 0 else 0)
                        sp = 0 if tbl is not None else h * (pct - 100) // 100
                        segs.append((page, k * CHARS, y, h, sp))
                        y += h + sp
                    y += sa
                    if laid_out_pages is not None and segs[0][0] > laid_out_pages:
                        continue
                    lsa = etree.SubElement(p, _q(HP, "linesegarray"))
                    for _, pos, vp, h, sp in segs:
                        etree.SubElement(lsa, _q(HP, "lineseg"), textpos=str(pos),
                                         vertpos=str(vp), vertsize=str(h), spacing=str(sp))
                blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            dst.writestr(info, blob)
    return out.getvalue()


def _hangul(fake, **kw) -> None:
    fake.convert = lambda data, fmt: fake_hangul_layout(data, **kw)


def _pages(path: Path) -> list[int]:
    """Page of each top-level paragraph's first line, per the fake layout."""
    root = etree.fromstring(
        zipfile.ZipFile(BytesIO(fake_hangul_layout(path.read_bytes()))).read(
            "Contents/section0.xml"
        )
    )
    out, page, prev = [], 1, -1
    for p in root.findall(_q(HP, "p")):
        segs = p.findall(f"{_q(HP, 'linesegarray')}/{_q(HP, 'lineseg')}")
        for k, g in enumerate(segs):
            vp = int(g.get("vertpos"))
            if vp < prev:
                page += 1
            prev = vp
            if k == 0:
                out.append(page)
    return out


#: Page 1 (spacing 500/500, line pitch 1600) holds the first line of the
#: 2-line paragraph 4; its second line spills alone onto page 2. Cutting the
#: page's paragraph spacing by 40% wins back 1800 ≥ the 1600 the line needs.
DOC = [("가" * 30, False)] * 2 + [("가" * 20, False), ("나" * 20, False), ("다" * 30, False)]


def test_fake_layout_spills_one_line(tmp_path: Path) -> None:
    doc = make_doc(tmp_path / "d.hwpx", DOC)
    assert _pages(doc) == [1, 1, 1, 1, 1, 2]
    assert _split(doc, 4)


def _split(path: Path, pi: int) -> bool:
    """True if paragraph *pi* breaks across pages in the fake layout."""
    root = etree.fromstring(zipfile.ZipFile(BytesIO(
        fake_hangul_layout(path.read_bytes()))).read("Contents/section0.xml"))
    vps = [int(g.get("vertpos")) for g in root.findall(_q(HP, "p"))[pi].iter(_q(HP, "lineseg"))]
    return vps != sorted(vps)


def test_pull_back_applies_and_verifies(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    _hangul(fake_hwp2pdf)
    doc = make_doc(tmp_path / "d.hwpx", DOC)
    original = doc.read_bytes()
    out = tmp_path / "out.hwpx"
    det = fit_pages(doc, out)
    assert det.engine == "hwp2pdf"
    applied = [f for f in det.fits if f.status == "applied"]
    assert len(applied) == 1, det.fits
    f = applied[0]
    assert f.kind == "pull" and f.page == 1 and f.engine == "hwp2pdf"
    assert (f.spacing_cut, f.line_cut) == (40, 0)
    assert f.gap_after is not None and f.gap_after >= 0
    assert doc.read_bytes() == original  # the source is never modified
    assert not _split(out, 4)  # the spilled line is back on page 1
    assert _pages(out) == [1, 1, 1, 1, 1, 2]


def test_applied_page_clones_para_pr_and_drops_linesegs(
    tmp_path: Path, fake_hwp2pdf, no_rhwp
) -> None:
    _hangul(fake_hwp2pdf)
    doc = make_doc(tmp_path / "d.hwpx", DOC)
    out = tmp_path / "out.hwpx"
    det = fit_pages(doc, out)
    f = next(f for f in det.fits if f.status == "applied")
    with zipfile.ZipFile(out) as z:
        header = etree.fromstring(z.read("Contents/header.xml"))
        sec = etree.fromstring(z.read("Contents/section0.xml"))
    props = header.find(".//" + _q(HH, "paraProperties"))
    pps = props.findall(_q(HH, "paraPr"))
    assert props.get("itemCnt") == str(len(pps))
    by_id = {pp.get("id"): pp for pp in pps}
    # the original paraPr is untouched; clones are appended at the end
    orig = by_id[BODY_PP]
    assert orig.find(".//" + _q(HC, "prev")).get("value") == "500"
    tops = sec.findall(_q(HP, "p"))
    for pi in f.paragraphs:
        ref = tops[pi].get("paraPrIDRef")
        assert ref != BODY_PP and int(ref) >= 92  # after the last original id (91)
        assert tops[pi].find(_q(HP, "linesegarray")) is None
        clone = by_id[ref]
        cut = 100 - f.spacing_cut
        assert clone.find(".//" + _q(HC, "prev")).get("value") == str(round(500 * cut / 100))
    untouched = [p for i, p in enumerate(tops) if i not in f.paragraphs]
    assert untouched and all(p.get("paraPrIDRef") == BODY_PP for p in untouched)


def test_dry_run_reports_without_writing(tmp_path: Path, fake_hwp2pdf, no_rhwp, capsys) -> None:
    _hangul(fake_hwp2pdf)
    doc = make_doc(tmp_path / "d.hwpx", DOC)
    original = doc.read_bytes()
    assert main(["fit", "pages", str(doc), "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert doc.read_bytes() == original
    assert payload["engine"] == "hwp2pdf" and payload["judged"] is True
    (f,) = payload["fits"]
    assert f["status"] == "planned" and f["fittable"] is True and f["kind"] == "pull"
    assert f["gap_pct"] >= 0 and f["gap_after_pct"] is not None
    assert payload["pages_by_engine"] == {"hwp2pdf": 2}


def test_cannot_fit_reports_only(tmp_path: Path, fake_hwp2pdf, no_rhwp, capsys) -> None:
    """A large table after a 30% band: reported, document unchanged."""
    _hangul(fake_hwp2pdf)
    doc = make_doc(tmp_path / "d.hwpx", [("가" * 50, False), ("TABLE", 12000),
                                          ("다" * 30, False)])
    out = tmp_path / "out.hwpx"
    det = fit_pages(doc, out)
    (f,) = det.fits
    assert f.kind == "gap" and not f.fittable and f.status == "unfittable"
    assert f.gap_pct >= 25
    assert det.applied == 0
    with zipfile.ZipFile(out) as z, zipfile.ZipFile(doc) as zs:
        assert z.read("Contents/section0.xml") == zs.read("Contents/section0.xml")
        assert z.read("Contents/header.xml") == zs.read("Contents/header.xml")
    assert main(["fit", "pages", str(doc), "--dry-run"]) == 0
    assert "수동 판단 필요" in capsys.readouterr().out


def test_limits_too_small_leave_page_alone(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    _hangul(fake_hwp2pdf)
    doc = make_doc(tmp_path / "d.hwpx", DOC)
    det = fit_pages(doc, tmp_path / "o.hwpx", max_spacing_cut=5, max_linespacing_cut=0)
    (f,) = det.fits
    assert f.status == "unfittable" and det.applied == 0


def test_intended_page_break_is_preserved(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    _hangul(fake_hwp2pdf)
    doc = make_doc(tmp_path / "d.hwpx", [("가" * 30, False)] * 2 + [("참고 1", True)]
                   + [("다" * 10, False)])
    out = tmp_path / "out.hwpx"
    det = fit_pages(doc, out)
    (f,) = det.fits
    assert f.kind == "forced" and f.status == "forced" and not f.fittable
    assert det.applied == 0
    with zipfile.ZipFile(out) as z:
        tops = etree.fromstring(z.read("Contents/section0.xml")).findall(_q(HP, "p"))
    assert [p.get("pageBreak") for p in tops] == ["0", "0", "0", "1", "0"]
    assert _pages(out) == [1, 1, 1, 2, 2]


def test_move_that_does_not_take_is_reverted(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    # Hangul (fake) ignores the cut: the chunk stays, the change is undone.
    _hangul(fake_hwp2pdf, ignore_cuts={str(i): (500, 500, 160) for i in range(92, 110)})
    doc = make_doc(tmp_path / "d.hwpx", DOC)
    out = tmp_path / "out.hwpx"
    det = fit_pages(doc, out)
    (f,) = det.fits
    assert f.status == "reverted" and det.applied == 0
    with zipfile.ZipFile(out) as z:
        tops = etree.fromstring(z.read("Contents/section0.xml")).findall(_q(HP, "p"))
    assert all(p.get("paraPrIDRef") == BODY_PP for p in tops)


def test_no_engine_exit_code(tmp_path: Path, no_rhwp, capsys) -> None:
    doc = make_doc(tmp_path / "d.hwpx", [("가" * 30, False)])
    assert main(["fit", "pages", str(doc), "--dry-run"]) == 3
    err = capsys.readouterr().err
    assert "판정 엔진 없음" in err and "대상 없음이 아님" in err and "rhwp 없음" in err
    assert main(["fit", "pages", str(doc), "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["engine"] == "none" and payload["judged"] is False


def test_hwp2pdf_busy_and_no_rhwp_is_no_engine(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    fake_hwp2pdf.hwp_running = ["Hwp.exe"]
    doc = make_doc(tmp_path / "d.hwpx", [("가" * 30, False)])
    det = fit_pages(doc, dry_run=True)
    assert det.engine == "none"
    assert any("한글이 실행 중" in n for n in det.notes)
    assert not any(r[1].startswith("/v1/jobs") for r in fake_hwp2pdf.requests)


def test_partial_hangul_layout_marks_rest_unjudged(
    tmp_path: Path, fake_hwp2pdf, no_rhwp
) -> None:
    """#65: Hangul laid out page 1 only; without rhwp the rest is named, not hidden."""
    _hangul(fake_hwp2pdf, laid_out_pages=1)
    doc = make_doc(tmp_path / "d.hwpx", DOC + [("다" * 30, False)] * 8)
    det = fit_pages(doc, dry_run=True)
    assert det.engine == "hwp2pdf"
    assert any("판정 못함" in n and "rhwp 없음" in n for n in det.notes)
    # page 1's boundary still needs page 2's first chunk — which Hangul did lay out
    assert det.pages.get("rhwp", 0) == 0


def test_write_fit_pages_without_engine_warns_and_writes(
    tmp_path: Path, no_rhwp, capsys
) -> None:
    md = tmp_path / "c.md"
    md.write_text("# 제목\n\n본문 문단입니다.\n", encoding="utf-8")
    out = tmp_path / "o.hwpx"
    rc = main(["write", str(md), "--fit-pages", "-o", str(out)])
    assert rc == 0 and out.is_file()
    assert "--fit-pages 미적용" in capsys.readouterr().err


def test_clone_para_pr_scales_every_branch() -> None:
    pp = etree.fromstring(_para_pr("7", 800, 400, 160))
    clone = _clone_para_pr(pp, "99", 50, 10)
    prevs = [e.get("value") for e in clone.iter(_q(HC, "prev"))]
    nexts = [e.get("value") for e in clone.iter(_q(HC, "next"))]
    spacing = [e.get("value") for e in clone.iter(_q(HH, "lineSpacing"))]
    assert clone.get("id") == "99" and pp.get("id") == "7"
    assert prevs == ["400", "800"] and nexts == ["200", "400"]
    assert spacing == ["144", "144"]


@pytest.mark.parametrize("cut", [0, 10])
def test_fit_tails_option_runs_first(tmp_path: Path, fake_hwp2pdf, no_rhwp, capsys, cut) -> None:
    _hangul(fake_hwp2pdf)
    doc = make_doc(tmp_path / "d.hwpx", DOC)
    out = tmp_path / "o.hwpx"
    rc = main(["fit", "pages", str(doc), "--fit-tails", "--max-linespacing-cut", str(cut),
               "-o", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert text.index("꼬리 줄 압축 먼저") < text.index("쪽 채움")


RHWP_DUMP = """문서 로드: x.hwpx (2페이지)

=== 페이지 1 (global_idx=0, section=0, page_num=1) ===
  body_area: x=83.1 y=113.4 w=627.4 h=266.7
  단 0 (items=3, used=250.0px, hwp_used≈0.0px, diff=+0.0px)
    FullParagraph  pi=0  h=12.0 (sb=0.0 lines=12.0 lh=12.0 ls=0.0 sa=0.0)  vpos=0  ls=1[ts=0 lh=900 th=900 bl=765 gap=0 cs=0 sw=47056]  "(빈)"
    Table          pi=1 ci=0  1x3  627.4x34.0px  wrap=TopAndBottom tac=true  vpos=900  ls=1[ts=0 lh=2551 th=2551 bl=2168 gap=720 cs=0 sw=47056]
    PartialParagraph  pi=2  lines=0..2  vpos=4000..5600  ls=3[first ts=0 lh=1000 th=1000 bl=850 gap=600 cs=0 sw=46056; last ts=60 lh=1000 th=1000 bl=850 gap=600 cs=0 sw=46056]

=== 페이지 2 (global_idx=1, section=0, page_num=2) ===
  body_area: x=83.1 y=113.4 w=627.4 h=266.7
  단 0 (items=2, used=60.0px, hwp_used≈0.0px, diff=+0.0px)
    PartialParagraph  pi=2  lines=2..3  vpos=0  ls=3[first ts=0 lh=1000 th=1000 bl=850 gap=600 cs=0 sw=46056; last ts=60 lh=1000 th=1000 bl=850 gap=600 cs=0 sw=46056]
    FullParagraph  pi=3  h=21.3 (sb=6.7 lines=13.3 lh=13.3 ls=0.0 sa=6.7)  vpos=2100  ls=1[ts=0 lh=1000 th=1000 bl=850 gap=600 cs=0 sw=46056]  "끝"
"""  # noqa: E501


def test_parse_rhwp_pages(tmp_path: Path) -> None:
    from hwp_agent.ops.fit_pages import _parse_rhwp_pages, _read_doc

    doc = _read_doc(make_doc(tmp_path / "d.hwpx", [("TABLE", 2551), ("가" * 30, False),
                                                   ("끝", False)]))
    pages = _parse_rhwp_pages(RHWP_DUMP, doc)
    assert [p.number for p in pages] == [1, 2]
    p1, p2 = pages
    assert p1.forced and not p2.forced  # section start vs. a natural break
    assert [(b.pi, b.start, len(b.lines), b.first, b.last) for b in p1.blocks] == [
        (0, 0, 1, True, True), (1, 0, 1, True, True), (2, 0, 2, True, False)]
    assert p1.blocks[1].lines == [(2550, 720)]  # 34.0px table, HWPUNIT
    assert p2.blocks[0].lines == [(1000, 600)] and p2.blocks[0].last
    assert p1.bottom == 18750  # 250px; the split paragraph adds no spacing-after
    assert p2.bottom == 4500 - 500  # 60px minus paragraph 3's spacing-after


def test_intended_break_splits_pages_at_equal_vertpos(tmp_path: Path) -> None:
    """review #78: a pageBreak starts a page even if vertpos does not drop."""
    from hwp_agent.ops.fit_pages import _hangul_pages, _read_doc

    doc = _read_doc(make_doc(tmp_path / "d.hwpx", [("참고 1", True)]))
    root = etree.fromstring(doc.sections["Contents/section0.xml"])
    for p in root.findall(_q(HP, "p")):
        lsa = etree.SubElement(p, _q(HP, "linesegarray"))
        etree.SubElement(lsa, _q(HP, "lineseg"), textpos="0", vertpos="500",
                         vertsize="1000", spacing="600")
    pages, uncovered = _hangul_pages(doc, {"Contents/section0.xml": etree.tostring(root)},
                                     "hwp2pdf")
    assert [p.number for p in pages] == [1, 2] and not uncovered
    assert pages[1].forced
