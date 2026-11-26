"""Cell padding of the tables write/build make is pinned, not the library default.

python-hwpx 2.11's ``add_table`` writes 0 on all four sides of a table's ``inMargin``
and each cell's ``cellMargin``; 6.12+ writes Hancom's 510/510/141/141. Our figure
tables and data-table column fit were laid out on 0, so the value is spelled out in
``author._add_table`` and these tests assert it — a library upgrade that changes the
default must not change the output silently.
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
from hwp_agent.ops import build_report

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TPL = ROOT / "src" / "hwp_agent" / "assets" / "default-template.hwpx"
GOLDEN = ROOT / "examples" / "정책과제-template.hwpx"
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
ZERO = {"left": "0", "right": "0", "top": "0", "bottom": "0"}

BODY = """# Ⅰ. 서론

<표 Ⅰ-1> 가상 여백 표

| 구분 | 값 |
|---|---|
| 가 | 1 |

자료: 가상 출처

[그림 Ⅰ-1] 가상 여백 그림

![가상](fig.png)

[그림 Ⅰ-2] 가상 빈 그림

![없음](missing.png)

자료: 가상 그림 출처
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


def _generated_tables(path: Path) -> list:
    """The tables holding the manuscript's text (not the template's own forms)."""
    out = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not re.fullmatch(r"Contents/section\d+\.xml", name):
                continue
            for tbl in etree.fromstring(z.read(name)).iter(f"{_HP}tbl"):
                text = "".join(t.text or "" for t in tbl.iter(f"{_HP}t"))
                if "가상" in text or tbl.find(f".//{_HP}pic") is not None:
                    out.append(tbl)
    return out


def _assert_pinned(tables: list) -> None:
    """Every generated table's inner margin — what Hangul pads a ``hasMargin="0"``
    cell with — is 0 (the brief's 요약 상자 sets its own 510/141, test_brief_meta)."""
    assert tables
    for tbl in tables:
        assert dict(tbl.find(f"{_HP}inMargin").attrib) == ZERO


def _margins(tables: list) -> list:
    """Each table's inMargin and every cell's hasMargin + cellMargin."""
    return [
        (dict(tbl.find(f"{_HP}inMargin").attrib),
         [(tc.get("hasMargin"), dict(tc.find(f"{_HP}cellMargin").attrib))
          for tc in tbl.iter(f"{_HP}tc")])
        for tbl in tables
    ]


def _manuscript(tmp_path: Path) -> Path:
    _png(tmp_path / "fig.png")
    md = tmp_path / "r.md"
    md.write_text(BODY, encoding="utf-8")
    return md


@pytest.mark.parametrize("template", [DEFAULT_TPL, GOLDEN], ids=["default", "golden"])
def test_write_tables_have_zero_cell_padding(tmp_path: Path, template: Path) -> None:
    if not template.is_file():
        pytest.skip("template absent")
    md = _manuscript(tmp_path)
    out = tmp_path / "w.hwpx"
    assert main(["write", str(md), "--template", str(template), "-o", str(out)]) == 0
    tables = _generated_tables(out)
    assert len(tables) >= 3  # the data table and both figure tables
    _assert_pinned(tables)


@pytest.mark.skipif(not GOLDEN.is_file(), reason="golden template absent")
def test_build_tables_have_zero_cell_padding(tmp_path: Path) -> None:
    _manuscript(tmp_path)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(
        {"template": str(GOLDEN), "output": "b.hwpx", "parts": {"body": "r.md"}}), "utf-8")
    build_report(manifest)
    _assert_pinned(_generated_tables(tmp_path / "b.hwpx"))


def test_padding_does_not_follow_the_library_default(tmp_path: Path, monkeypatch) -> None:
    """Simulate the 6.12+ default (510/510/141/141): table and cell margins come out
    exactly as with the 2.11 default."""
    import hwpx.oxml._document_impl as impl

    if not hasattr(impl, "_default_cell_margin_attributes"):
        pytest.skip("python-hwpx internals differ")  # the tests above still pin inMargin
    md = _manuscript(tmp_path)
    before = tmp_path / "before.hwpx"
    assert main(["write", str(md), "--template", str(DEFAULT_TPL), "-o", str(before)]) == 0
    hancom = {"left": "510", "right": "510", "top": "141", "bottom": "141"}
    monkeypatch.setattr(impl, "_default_cell_margin_attributes", lambda: dict(hancom))
    after = tmp_path / "after.hwpx"
    assert main(["write", str(md), "--template", str(DEFAULT_TPL), "-o", str(after)]) == 0
    tables = _generated_tables(after)
    assert len(tables) >= 3
    assert _margins(tables) == _margins(_generated_tables(before))
    _assert_pinned(tables)
