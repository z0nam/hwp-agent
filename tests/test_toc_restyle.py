"""`hwp-agent toc restyle` — undo the blue + underline a Hangul 차례 새로 고침 paints.

Fixture: the header and 목차 section of a test build a user refreshed and saved in
Hangul (tests/data/toc_refreshed, synthetic text), dropped into the repo's default
template container. A body hyperlink in a blue char shape is added outside the
차례 fields to check it is left alone.
"""

from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops import restyle_toc

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "tests" / "data" / "toc_refreshed"
CONTAINER = REPO_ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HH = "{http://www.hancom.co.kr/hwpml/2011/head}"


def _blue_char(header: etree._Element) -> str:
    for c in header.iter(f"{_HH}charPr"):
        ul = c.find(f"{_HH}underline")
        if c.get("textColor") == "#0000FF" and ul is not None and ul.get("type") == "BOTTOM":
            return c.get("id")
    raise AssertionError("fixture has no blue link char shape")


def _refreshed(tmp_path: Path) -> Path:
    header = gzip.decompress((DATA / "header.xml.gz").read_bytes())
    section = etree.fromstring(gzip.decompress((DATA / "section1.xml.gz").read_bytes()))
    blue = _blue_char(etree.fromstring(header))
    body_link = etree.fromstring(
        f'<hp:p xmlns:hp="{_HP[1:-1]}" id="1" paraPrIDRef="0" styleIDRef="0" pageBreak="0" '
        f'columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:ctrl>'
        f'<hp:fieldBegin id="77" type="HYPERLINK" name="" editable="0" dirty="0" '
        f'zorder="-1" fieldid="627600491" metaTag=""><hp:parameters cnt="1" name="">'
        f'<hp:stringParam name="Command">https\\://www.ji.re.kr;1;0;0;</hp:stringParam>'
        f"</hp:parameters></hp:fieldBegin></hp:ctrl></hp:run>"
        f'<hp:run charPrIDRef="{blue}"><hp:t>본문 링크</hp:t></hp:run>'
        f'<hp:run charPrIDRef="0"><hp:ctrl><hp:fieldEnd beginIDRef="77" fieldid="627600491"/>'
        f"</hp:ctrl><hp:t/></hp:run></hp:p>"
    )
    section.append(body_link)
    out = tmp_path / "refreshed.hwpx"
    with zipfile.ZipFile(CONTAINER) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "Contents/header.xml":
                data = header
            elif info.filename == "Contents/section0.xml":
                data = etree.tostring(section, xml_declaration=True, encoding="UTF-8",
                                      standalone=True)
            zout.writestr(info, data)
    return out


def _state(path: Path):
    with zipfile.ZipFile(path) as z:
        head = etree.fromstring(z.read("Contents/header.xml"))
        sec = etree.fromstring(z.read("Contents/section0.xml"))
    styles = {s.get("id"): s for s in head.iter(f"{_HH}style")}
    return head, sec, styles


def _entries(sec, styles):
    """(style name, [run charPr ids], text) of every 차례-styled paragraph."""
    out = []
    for p in sec.findall(f"{_HP}p"):
        name = styles[p.get("styleIDRef")].get("name")
        if name.startswith("차례"):
            text = "".join(t.text or "" for t in p.iter(f"{_HP}t")).strip()
            out.append((name, [r.get("charPrIDRef") for r in p.findall(f"{_HP}run")], text))
    return out


def test_restyle_sets_link_text_back_to_the_toc_style(tmp_path: Path) -> None:
    src = _refreshed(tmp_path)
    head, sec, styles = _state(src)
    blue = _blue_char(head)
    before = _entries(sec, styles)
    assert any(blue in runs or any(head.find(f".//{_HH}charPr[@id='{r}']").get("textColor")
                                   == "#0000FF" for r in runs) for _, runs, _ in before)

    res = restyle_toc(src, tmp_path / "out.hwpx")
    assert res.fields == 2 and res.runs == 7 and res.titles == 0
    head, sec, styles = _state(tmp_path / "out.hwpx")
    by_name = {s.get("name"): s.get("charPrIDRef") for s in styles.values()}
    for name, runs, text in _entries(sec, styles):
        if name == "차례 제목":
            continue
        assert set(runs) == {by_name[name]}, (name, text, runs)
    # links, page numbers and paragraph shapes stay
    def links(root):
        return [fb.findtext(f".//{_HP}stringParam") for fb in root.iter(f"{_HP}fieldBegin")
                if fb.get("type") == "HYPERLINK"]

    assert links(sec) == links(_state(src)[1])
    assert [p.get("paraPrIDRef") for p in sec.findall(f"{_HP}p")] == [
        p.get("paraPrIDRef") for p in _state(src)[1].findall(f"{_HP}p")]
    texts = [t for _, _, t in _entries(sec, styles)]
    assert "Ⅰ. 서론" in texts and "<제목 차례>" in texts
    pages = [tab.tail for tab in sec.iter(f"{_HP}tab") if (tab.tail or "").strip()]
    assert pages == [tab.tail for tab in _state(src)[1].iter(f"{_HP}tab")
                     if (tab.tail or "").strip()] and "4" in pages
    # the body hyperlink outside the 차례 fields keeps its blue look
    body = sec.findall(f"{_HP}p")[-1]
    assert body.findall(f"{_HP}run")[1].get("charPrIDRef") == _blue_char(head)


def test_restyle_is_idempotent(tmp_path: Path) -> None:
    src = _refreshed(tmp_path)
    once, twice = tmp_path / "1.hwpx", tmp_path / "2.hwpx"
    restyle_toc(src, once)
    res = restyle_toc(once, twice)
    assert not res.changed
    assert once.read_bytes() == twice.read_bytes()


def test_drop_titles_keeps_the_fields_whole(tmp_path: Path) -> None:
    src = _refreshed(tmp_path)
    res = restyle_toc(src, tmp_path / "out.hwpx", drop_titles=True)
    assert res.titles == 2
    _, sec, styles = _state(tmp_path / "out.hwpx")
    texts = [t for _, _, t in _entries(sec, styles)]
    assert "<제목 차례>" not in texts and "<그림 차례>" not in texts
    begins = [fb.get("id") for fb in sec.iter(f"{_HP}fieldBegin")
              if fb.get("type") == "TABLEOFCONTENTS"]
    ends = [fe.get("beginIDRef") for fe in sec.iter(f"{_HP}fieldEnd")
            if fe.get("fieldid") == "628387683"]
    assert len(begins) == 2 and sorted(begins) == sorted(ends)
    assert not restyle_toc(tmp_path / "out.hwpx", tmp_path / "again.hwpx",
                           drop_titles=True).changed


def test_no_toc_field_is_a_no_op(tmp_path: Path) -> None:
    dst = tmp_path / "copy.hwpx"
    res = restyle_toc(CONTAINER, dst)
    assert res.fields == 0 and not res.changed
    assert dst.read_bytes() == CONTAINER.read_bytes()


def test_cli(tmp_path: Path, capsys) -> None:
    src = _refreshed(tmp_path)
    assert main(["toc", "restyle", str(src), "-o", str(tmp_path / "o.hwpx")]) == 0
    assert "링크 글자 7곳" in capsys.readouterr().out
    assert main(["toc", "restyle", str(tmp_path / "o.hwpx"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"fields": 2, "runs": 0, "titles": 0}
