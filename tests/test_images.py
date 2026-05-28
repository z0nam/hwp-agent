"""Tests for hwp_agent.ops.images (figure image listing + in-place replace)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from hwp_agent.cli.main import build_parser
from hwp_agent.ops import list_images, parse_alt_text, read_image_size, replace_image
from hwp_agent.ops.images import _manifest_href, _read_text, sniff_format

REPO_ROOT = Path(__file__).resolve().parents[1]
JPG_SLOT = REPO_ROOT / "tests" / "fixtures" / "sample_hwpx.hwpx"  # single jpg pic (image1)


# --------------------------------------------------------------------------- #
# tiny header-only encoders (Pillow-free) so size/format sniffing has real input
# --------------------------------------------------------------------------- #
def _jpeg(w: int, h: int) -> bytes:
    # SOI + SOF0 carrying height then width, padded past the parser's lookahead
    return (
        b"\xff\xd8"
        + b"\xff\xc0\x00\x11\x08"
        + h.to_bytes(2, "big")
        + w.to_bytes(2, "big")
        + b"\x00" * 16
    )


def _png(w: int, h: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + w.to_bytes(4, "big")
        + h.to_bytes(4, "big")
        + b"\x00" * 8
    )


def _bmp(w: int, h: int) -> bytes:
    buf = bytearray(b"BM" + b"\x00" * 24)
    buf[18:22] = w.to_bytes(4, "little", signed=True)
    buf[22:26] = h.to_bytes(4, "little", signed=True)
    return bytes(buf)


# --------------------------------------------------------------------------- #
# inspection helpers
# --------------------------------------------------------------------------- #
def test_sniff_format() -> None:
    assert sniff_format(_png(1, 1)) == "png"
    assert sniff_format(_jpeg(1, 1)) == "jpeg"
    assert sniff_format(_bmp(1, 1)) == "bmp"
    assert sniff_format(b"not an image") is None


def test_read_image_size_header_fallback() -> None:
    assert read_image_size(_png(640, 480)) == (640, 480)
    assert read_image_size(_jpeg(200, 100)) == (200, 100)
    assert read_image_size(_bmp(320, 240)) == (320, 240)


def test_parse_alt_text() -> None:
    comment = "원본 그림의 이름: chart.png\n원본 그림의 크기: 가로 800pixel, 세로 600pixel"
    name, w, h = parse_alt_text(comment)
    assert (name, w, h) == ("chart.png", 800, 600)
    assert parse_alt_text("") == (None, None, None)


def test_list_images_finds_jpg_slot() -> None:
    pics = list_images(JPG_SLOT)
    assert len(pics) == 1
    p = pics[0]
    assert p.ref == "image1"
    assert p.slot_format == "jpg"
    assert (p.px_w, p.px_h) == (2244, 3071)
    assert p.extent_w > 0 and p.extent_h > 0


# --------------------------------------------------------------------------- #
# replacement
# --------------------------------------------------------------------------- #
def _bindata_bytes(path: Path, ref: str) -> bytes:
    with zipfile.ZipFile(path) as zf:
        href = _manifest_href(_read_text(zf, "Contents/content.hpf"), ref)
        assert href is not None
        return zf.read(href)


def test_replace_aspect_swaps_bytes_and_recomputes_height(tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    img = tmp_path / "new.jpg"
    img.write_bytes(_jpeg(200, 100))  # ratio 2.0, unlike the slot's tall portrait

    before = list_images(JPG_SLOT)[0]
    result = replace_image(JPG_SLOT, ref="image1", image=img, fit="aspect", output=out)

    assert result.replaced == 1
    assert result.outcomes[0].status == "replaced"
    assert _bindata_bytes(out, "image1") == img.read_bytes()  # bytes actually swapped

    after = list_images(out)[0]
    assert after.extent_w == before.extent_w  # width kept
    assert after.extent_h == round(before.extent_w * 100 / 200)  # height follows new aspect
    assert not JPG_SLOT.samefile(out) and JPG_SLOT.exists()  # source untouched


def test_replace_preserves_zip_container(tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    img = tmp_path / "new.jpg"
    img.write_bytes(_jpeg(200, 100))
    replace_image(JPG_SLOT, ref="image1", image=img, output=out)

    with zipfile.ZipFile(JPG_SLOT) as zsrc, zipfile.ZipFile(out) as zout:
        src_names = [i.filename for i in zsrc.infolist()]
        out_infos = zout.infolist()
        assert [i.filename for i in out_infos] == src_names  # order preserved
    assert out_infos[0].filename == "mimetype"
    assert out_infos[0].compress_type == zipfile.ZIP_STORED


def test_replace_fit_none_keeps_extent(tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    img = tmp_path / "new.jpg"
    img.write_bytes(_jpeg(200, 100))
    before = list_images(JPG_SLOT)[0]
    replace_image(JPG_SLOT, ref="image1", image=img, fit="none", output=out)
    after = list_images(out)[0]
    assert (after.extent_w, after.extent_h) == (before.extent_w, before.extent_h)


def test_replace_format_mismatch_refuses(tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    img = tmp_path / "new.png"
    img.write_bytes(_png(200, 100))  # PNG into a JPG slot
    result = replace_image(JPG_SLOT, ref="image1", image=img, output=out)
    assert result.replaced == 0
    assert result.outcomes[0].status == "format_mismatch"
    assert not out.exists()  # nothing written on refusal


def test_replace_unmatched_ref(tmp_path: Path) -> None:
    img = tmp_path / "new.jpg"
    img.write_bytes(_jpeg(10, 10))
    result = replace_image(JPG_SLOT, ref="image999", image=img, output=tmp_path / "o.hwpx")
    assert result.replaced == 0
    assert result.outcomes[0].status == "unmatched"


def test_replace_missing_file(tmp_path: Path) -> None:
    result = replace_image(
        JPG_SLOT, ref="image1", image=tmp_path / "nope.jpg", output=tmp_path / "o.hwpx"
    )
    assert result.replaced == 0
    assert result.outcomes[0].status == "missing_file"


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_cli_image_parser() -> None:
    args = build_parser().parse_args(["image", "list", str(JPG_SLOT), "--json"])
    assert args.action == "list" and args.json
    args = build_parser().parse_args(
        ["image", "replace", str(JPG_SLOT), "new.jpg", "--ref", "image1"]
    )
    assert args.action == "replace" and args.ref == "image1" and args.fit == "aspect"
