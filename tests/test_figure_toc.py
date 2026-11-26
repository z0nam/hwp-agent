"""Issues #119/#128: the 그림 차례 is a Hangul 차례 field over the figure tables.

Hangul's 차례 새로 고침 collects a captioned figure table (``numberingType="PICTURE"``)
into the 그림 차례 and links its entry to the table — checked in Hangul on a build
(#128); #119's "tables are not collected" had only been inferred.

All manuscripts here are synthetic; images are tiny generated PNGs.
"""

from __future__ import annotations

import json
import struct
import zipfile
import zlib
from pathlib import Path

from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops import build_report
from hwp_agent.ops.figures import is_figure_table
from hwp_agent.ops.toc import figure_toc_command

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "examples" / "정책과제-template.hwpx"
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

BODY = """# Ⅰ. 서론

## 1. 배경

본문 문장임

![넓은 가상 그림](wide.png){#fig_wide}
자료: 가상 자료

![세로로 긴 가상 그림](tall.png)

# Ⅱ. 분석

## 1. 현황

![두 장 나란히 가상 그림](wide.png) ![](sq.png){#fig_pair}
주) 좌: 가, 우: 나
"""


def _png(path: Path, w: int, h: int) -> Path:
    raw = b"".join(b"\x00" + b"\x80\x80\xff" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def _images(d: Path) -> None:
    _png(d / "wide.png", 40, 25)
    _png(d / "tall.png", 20, 45)
    _png(d / "sq.png", 25, 25)


def _manifest(d: Path, body: str = BODY) -> Path:
    _images(d)
    (d / "body.md").write_text(body, encoding="utf-8")
    m = {"template": str(GOLDEN), "output": "out.hwpx", "parts": {"body": "body.md"}}
    (d / "m.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    return d / "m.json"


def _sections(path: Path) -> dict[str, etree._Element]:
    with zipfile.ZipFile(path) as z:
        return {n: etree.fromstring(z.read(n)) for n in sorted(z.namelist())
                if n.startswith("Contents/section") and n.endswith(".xml")}


def _figure_tables(path: Path) -> list:
    return [t for root in _sections(path).values() for t in root.iter(f"{_HP}tbl")
            if is_figure_table(t)]


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(f"{_HP}t"))




def _toc_root(path: Path):
    return _sections(path)["Contents/section1.xml"]


def _figure_field(root) -> tuple[list[str], list[str]]:
    """(그림 차례 field commands, link targets of its entries)."""
    paras = root.findall(f"{_HP}p")
    head = next(i for i, p in enumerate(paras) if _text(p).strip() == "그림 차례")
    cmds, targets = [], []
    for p in paras[head + 1:]:
        for sp in p.iter(f"{_HP}stringParam"):
            if sp.get("name") != "Command":
                continue
            if sp.text.startswith("TableOfContents"):
                cmds.append(sp.text)
            elif sp.text.startswith("?#"):
                targets.append(sp.text[2:].split(";")[0])
    return cmds, targets


def test_figure_list_is_a_hangul_field_linked_to_the_figure_tables(tmp_path: Path) -> None:
    res = build_report(_manifest(tmp_path), literal_numbering=True)
    assert res.toc["figures"] == 3 and res.toc["figure_field"] is True
    out = tmp_path / "out.hwpx"
    cmds, targets = _figure_field(_toc_root(out))
    assert cmds == [figure_toc_command(int(cmds[0].split("ContentsLeader:int:")[1][0]))]
    assert "ContentsMake:uint:4 " in cmds[0]
    tables = _figure_tables(out)
    # the caption and number stay on the figure table, no picture group (#128)
    assert [t.get("numberingType") for t in tables] == ["PICTURE"] * 3
    assert all(t.find(f"{_HP}caption") is not None for t in tables)
    assert not any(root.find(f".//{_HP}container") is not None
                   for root in _sections(out).values())
    assert targets == [t.get("id") for t in tables]


def test_figure_field_also_in_default_numbering(tmp_path: Path) -> None:
    """Captions don't depend on outline paragraphs, so the 그림 차례 is a field in
    the default mode too (like the 표 차례)."""
    res = build_report(_manifest(tmp_path))
    assert res.toc["field"] is False
    assert res.toc["figure_field"] is True


def test_placeholder_figure_is_in_the_field_too(tmp_path: Path) -> None:
    """A missing-image figure keeps a captioned PICTURE table like any other, so the
    list stays a field."""
    res = build_report(_manifest(tmp_path, BODY + "\n![없는 그림](none.png)\n"))
    assert res.toc["figures"] == 4 and res.toc["figure_field"] is True
    cmds, targets = _figure_field(_toc_root(tmp_path / "out.hwpx"))
    assert len(cmds) == 1 and len(targets) == 4


def test_cli_build_keeps_figure_numbering_and_says_how_pages_fill(
    tmp_path: Path, capsys
) -> None:
    """#109's neutralize pass leaves captioned figure tables numbered; the CLI tells
    the user the 그림 차례 fills on refresh."""
    rc = main(["build", str(_manifest(tmp_path)), "--literal-numbering"])
    assert rc == 0
    tables = _figure_tables(tmp_path / "out.hwpx")
    assert [t.get("numberingType") for t in tables] == ["PICTURE"] * 3
    out = capsys.readouterr().out
    assert "차례·그림 차례는 한글에서 차례 새로 고침" in out
