"""Sample lettering in the 과제 forms' master-page pictures, and the title slot (#95)."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import add_running_title_slot, clean_form_backgrounds, finalize_report
from hwp_agent.ops import form_backgrounds as fb
from hwp_agent.ops.author import fill_from_markdown

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "examples" / "정책과제-template.hwpx"
HOUSE = sorted((ROOT / "examples" / "house-forms").glob("*과제_서식.normalized.hwpx"))

MD = "# Ⅰ. 서론\n\n본문 문장입니다.\n\n# Ⅱ. 분석\n\n본문 문장입니다.\n"


def _parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _mp_texts(path: Path) -> list[str]:
    return [
        v.decode("utf-8")
        for n, v in _parts(path).items()
        if n.startswith("Contents/masterpage") and b"VERTICAL" in v
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
    assert sum("<hp:t>가상 보고서 제목</hp:t>" in t for t in texts) == 4
    assert not any(fb.SLOT in t for t in texts)


def test_write_without_meta_blanks_running_title(tmp_path: Path) -> None:
    from hwp_agent.cli.main import main

    md = tmp_path / "doc.md"
    md.write_text(MD, encoding="utf-8")
    out = tmp_path / "out.hwpx"
    assert main(["write", str(md), "--template", str(GOLDEN), "-o", str(out)]) == 0
    assert not any("running_title" in t for t in _mp_texts(out))


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
