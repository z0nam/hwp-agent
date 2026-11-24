"""hwp-agent sign: stamp a signature image on a PDF's "(인)" mark (issue #88)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from hwp_agent.cli.main import main  # noqa: E402
from hwp_agent.ops.sign import _ink_pixmap, sign_pdf  # noqa: E402


def _form(path: Path, lines: list[tuple[int, float, str]]) -> Path:
    """A PDF with Korean text lines: (page, y, text)."""
    doc = fitz.open()
    for _ in range(max(p for p, _, _ in lines)):
        doc.new_page(width=595, height=842)
    for p, y, text in lines:
        doc[p - 1].insert_text((300, y), text, fontname="korea", fontsize=11)
    doc.save(str(path))
    return path


def _signature(path: Path, *, alpha: bool = True) -> Path:
    """A 200x100 image with a dark 'ink' block at (50..150, 30..70) on blank."""
    paper = b"\x00\x00\x00\x00" if alpha else b"\xff\xff\xff"
    ink = b"\x14\x14\x78\xff" if alpha else b"\x14\x14\x78"
    rows = (
        (ink if 30 <= y < 70 and 50 <= x < 150 else paper)
        for y in range(100) for x in range(200)
    )
    pix = fitz.Pixmap(fitz.csRGB, 200, 100, b"".join(rows), alpha)
    pix.save(str(path))
    return path


def _images_on(pdf: Path) -> list[tuple[int, fitz.Rect]]:
    doc = fitz.open(str(pdf))
    out = [(i + 1, r) for i, page in enumerate(doc)
           for img in page.get_images() for r in page.get_image_rects(img[0])]
    doc.close()
    return out


def test_ink_crop_transparent_png(tmp_path: Path) -> None:
    ink = _ink_pixmap(_signature(tmp_path / "s.png"))
    assert (ink.width, ink.height, ink.alpha) == (100, 40, 1)


def test_ink_crop_opaque_scan_gets_transparent_paper(tmp_path: Path) -> None:
    ink = _ink_pixmap(_signature(tmp_path / "s.png", alpha=False))
    assert (ink.width, ink.height, ink.alpha) == (100, 40, 1)


def test_signs_last_mark_centred(tmp_path: Path) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "성명: 홍길동 (인)"), (2, 700, "작성자 (인)")])
    out = tmp_path / "signed.pdf"
    res = sign_pdf(pdf, out, _signature(tmp_path / "s.png"))
    assert res.ok and len(res.placements) == 1
    pl = res.placements[0]
    assert pl.page == 2 and pl.anchor == "(인)"
    (page, rect), = _images_on(out)
    assert page == 2
    assert rect.width == pytest.approx(72, abs=0.5)
    assert rect.height == pytest.approx(72 * 40 / 100, abs=0.5)
    ax0, ay0, ax1, ay1 = pl.anchor_rect
    assert (rect.x0 + rect.x1) / 2 == pytest.approx((ax0 + ax1) / 2, abs=0.5)
    assert (rect.y0 + rect.y1) / 2 == pytest.approx((ay0 + ay1) / 2, abs=0.5)
    # the mark's text is still there (signature is drawn over it, not replacing it)
    assert "(인)" in fitz.open(str(out))[1].get_text()


def test_all_and_page_filter(tmp_path: Path) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "(인)"), (1, 500, "(인)"), (2, 700, "(인)")])
    sig = _signature(tmp_path / "s.png")
    res = sign_pdf(pdf, tmp_path / "a.pdf", sig, every=True)
    assert [p.page for p in res.placements] == [1, 1, 2]
    res = sign_pdf(pdf, tmp_path / "b.pdf", sig, page=1)
    assert [p.page for p in res.placements] == [1]
    assert res.placements[0].anchor_rect[1] > 400  # last on page 1


def test_anchor_fallback_order(tmp_path: Path) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "서명란 안내"), (1, 600, "신청인 (서명)")])
    res = sign_pdf(pdf, tmp_path / "o.pdf", _signature(tmp_path / "s.png"))
    assert res.placements[0].anchor == "(서명)"


def test_no_mark_warns_and_writes_nothing(tmp_path: Path) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "도장 없음")])
    out = tmp_path / "o.pdf"
    res = sign_pdf(pdf, out, _signature(tmp_path / "s.png"))
    assert not res.ok and res.warnings and not out.exists()


def test_dry_run_preview_only(tmp_path: Path) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "(인)")])
    prev = tmp_path / "prev.png"
    res = sign_pdf(pdf, None, _signature(tmp_path / "s.png"), preview=prev)
    assert res.ok and res.previews == [prev] and prev.is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.pdf", "prev.png", "s.png"]


def test_cli_uses_profile_signature(tmp_path: Path, capsys) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "(인)")])
    sig = _signature(tmp_path / "s.png")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"name": "x", "signature_image": str(sig)}), encoding="utf-8")
    rc = main(["sign", str(pdf), "--profile", str(profile), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["output"].endswith("f_서명.pdf") and len(report["placements"]) == 1
    assert Path(report["output"]).is_file()


def test_cli_without_signature_is_clear_error(tmp_path: Path, capsys) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "(인)")])
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    assert main(["sign", str(pdf), "--profile", str(profile)]) == 2
    assert "signature_image" in capsys.readouterr().err


def test_cli_no_mark_exit_1(tmp_path: Path) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "없음")])
    sig = _signature(tmp_path / "s.png")
    assert main(["sign", str(pdf), "--image", str(sig)]) == 1
    assert not (tmp_path / "f_서명.pdf").exists()


def test_mark_with_odd_spacing_is_found(tmp_path: Path) -> None:
    """rhwp writes "(인)" as "( 인)"; forms print "( 인 )" — spacing must not matter."""
    pdf = _form(tmp_path / "f.pdf", [(1, 300, "성명 홍길동 ( 인 )")])
    res = sign_pdf(pdf, tmp_path / "o.pdf", _signature(tmp_path / "s.png"))
    assert res.ok and res.placements[0].anchor == "(인)"


def test_opaque_rgba_canvas_gets_transparent_paper(tmp_path: Path) -> None:
    """An RGBA PNG whose alpha is 255 everywhere is a white canvas, not a cut-out."""
    rows = (
        (b"\x14\x14\x78\xff" if 30 <= y < 70 and 50 <= x < 150 else b"\xff\xff\xff\xff")
        for y in range(100) for x in range(200)
    )
    path = tmp_path / "s.png"
    fitz.Pixmap(fitz.csRGB, 200, 100, b"".join(rows), True).save(str(path))
    ink = _ink_pixmap(path)
    assert (ink.width, ink.height, ink.alpha) == (100, 40, 1)


def test_preview_on_rotated_page(tmp_path: Path) -> None:
    pdf = _form(tmp_path / "f.pdf", [(1, 800, "성명 (인)")])
    doc = fitz.open(str(pdf))
    doc[0].set_rotation(90)
    rotated = tmp_path / "r.pdf"
    doc.save(str(rotated))
    out, prev = tmp_path / "o.pdf", tmp_path / "p.png"
    res = sign_pdf(rotated, out, _signature(tmp_path / "s.png"), preview=prev)
    assert res.ok and out.is_file() and prev.is_file()
    # the preview shows the mark, not some other part of the page
    shot = fitz.Pixmap(str(prev))
    assert shot.width > 50 and shot.height > 50
