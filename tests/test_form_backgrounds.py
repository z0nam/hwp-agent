"""Sample lettering in the 과제 forms' master-page pictures, and the title slot (#95)."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.ops import add_running_title_slot, clean_form_backgrounds, finalize_report
from hwp_agent.ops import form_backgrounds as fb
from hwp_agent.ops.author import fill_from_markdown

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "examples" / "정책과제-template.hwpx"
HOUSE = sorted((ROOT / "examples" / "house-forms").glob("*과제_서식.normalized.hwpx"))

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
MD = "# Ⅰ. 서론\n\n본문 문장입니다.\n\n# Ⅱ. 분석\n\n본문 문장입니다.\n"


def _parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _mp_texts(path: Path) -> list[str]:
    return [
        v.decode("utf-8")
        for n, v in _parts(path).items()
        if n.startswith("Contents/masterpage") and fb.TITLE_ART_COMMENT.encode() in v
    ]


def test_clean_assets_match_their_map() -> None:
    for name in fb._map().values():
        data = (fb.ASSET_DIR / name).read_bytes()
        assert data.startswith(b"\xff\xd8")  # a JPEG
        assert len(data) < 300_000


def test_clean_form_backgrounds_swaps_only_known_pictures(tmp_path: Path, monkeypatch) -> None:
    # stand in a picture of the golden template for a "lettered" original
    parts = _parts(GOLDEN)
    victim = "BinData/image3.jpg"
    fake = {hashlib.sha256(parts[victim]).hexdigest(): "even-white.jpg"}
    fb._map.cache_clear()
    monkeypatch.setattr(fb, "_map", lambda: fake)
    out = tmp_path / "out.hwpx"
    assert clean_form_backgrounds(GOLDEN, out) == [victim]
    after = _parts(out)
    assert after[victim] == (fb.ASSET_DIR / "even-white.jpg").read_bytes()
    assert {n for n in parts if parts[n] != after[n]} == {victim}
    with zipfile.ZipFile(GOLDEN) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]


@pytest.mark.parametrize("path", [GOLDEN, *HOUSE], ids=lambda p: p.name)
def test_shipped_forms_have_clean_backgrounds_and_title_slots(path: Path) -> None:
    parts = _parts(path)
    lettered = set(fb._map())
    assert not [n for n, v in parts.items()
                if n.startswith("BinData/") and hashlib.sha256(v).hexdigest() in lettered]
    slots = [t for t in _mp_texts(path) if fb.SLOT in t]
    assert len(slots) == 4  # every even master page with the cleaned title picture
    assert add_running_title_slot(path) == []  # already there: a no-op


def test_write_fills_running_title_from_meta(tmp_path: Path) -> None:
    from hwp_agent.cli.main import main

    md = tmp_path / "doc.md"
    md.write_text('---\ntitle: "가상 보고서 제목"\n---\n' + MD, encoding="utf-8")
    out = tmp_path / "out.hwpx"
    assert main(["write", str(md), "--template", str(GOLDEN), "-o", str(out)]) == 0
    texts = _mp_texts(out)
    assert sum('text="가상 보고서 제목"' in t for t in texts) == 4
    assert not any(fb.SLOT in t for t in texts)
    # sized to the filled title, not the token (a 글맵시 stretches text to its box)
    art = etree.fromstring(texts[0].encode()).find(f".//{{{HP}}}textart")
    em = int(art.find(f"{{{HP}}}curSz").get("height"))
    want = int((7 * 872 + 2 * 290) * em * 0.92) // 1000
    assert int(art.find(f"{{{HP}}}curSz").get("width")) == want


def test_write_without_meta_blanks_running_title(tmp_path: Path) -> None:
    from hwp_agent.cli.main import main

    md = tmp_path / "doc.md"
    md.write_text(MD, encoding="utf-8")
    out = tmp_path / "out.hwpx"
    assert main(["write", str(md), "--template", str(GOLDEN), "-o", str(out)]) == 0
    assert not any("running_title" in t for t in _mp_texts(out))
    with zipfile.ZipFile(out) as z:  # the empty 글맵시 is gone, not left as a 0-length box
        assert not any(b"<hp:textart" in z.read(n) and fb.TITLE_ART_COMMENT.encode() in z.read(n)
                       for n in z.namelist() if n.startswith("Contents/masterpage"))


def test_finalize_report_matches_cli_write(tmp_path: Path) -> None:
    from hwp_agent.cli.main import main

    lib = tmp_path / "lib.hwpx"
    fill_from_markdown(GOLDEN, MD, output=lib)
    fin = finalize_report(lib)
    assert fin.wings is not None and fin.wings.titles == ["Ⅰ. 서론", "Ⅱ. 분석"]
    assert fin.warnings == []

    md = tmp_path / "doc.md"
    md.write_text(MD, encoding="utf-8")
    cli = tmp_path / "cli.hwpx"
    assert main(["write", str(md), "--template", str(GOLDEN), "-o", str(cli)]) == 0

    def wings(path: Path) -> int:
        return sum(v.count(b'applyPageType="ODD"') for n, v in _parts(path).items()
                   if n.startswith("Contents/section"))

    assert wings(lib) == wings(cli) > 0


def test_finalize_report_without_chapter_header(tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    fill_from_markdown(GOLDEN, MD, output=out)
    assert finalize_report(out, chapter_header=False).wings is None


def test_title_art_geometry_follows_hangul() -> None:
    """Values Hangul itself writes for a turned 글맵시 (re-save, 2026-11-25)."""
    from hwp_agent.ops.chapter_header import _title_art

    for angle, rot in ((90, ("0", "-1", "600", "1", "0", "0")),
                       (270, ("0", "1", "0", "-1", "0", "W"))):
        art = etree.fromstring(_title_art(
            text='가"나', shape_id=10, horz=100, vert=200, em=600, angle=angle,
            face="KoPubWorld돋움체 Bold", color="#06004B", bold=True,
        ))
        w = int((2 * 872 + 320) * 600 * 0.92) // 1000
        assert art.get("text") == '가"나'
        sz = art.find(f"{{{HP}}}sz")
        assert (sz.get("width"), sz.get("height")) == ("600", str(w))  # after turning
        ri = art.find(f"{{{HP}}}rotationInfo")
        assert (ri.get("angle"), ri.get("centerX"), ri.get("centerY")) == (
            str(angle), "300", str(w // 2))
        m = art.find(f".//{{{fb._HC}}}rotMatrix")
        assert tuple(m.get(f"e{i}") for i in range(1, 7)) == tuple(
            str(w) if v == "W" else v for v in rot)
        assert art.find(f".//{{{HP}}}textartPr").get("fontStyle") == "진하게"  # as the heading
