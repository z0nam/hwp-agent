"""Test helper: a real inline picture run (#137).

Tests that need "a paragraph holding a picture" used to append a bare
``<hp:pic/>``. python-hwpx >= 6 refuses to save such a package (Hangul crashes
on a picture without renderingInfo matrices or ``hc:img``), so build the same
picture ``write`` emits instead: a 2×2 PNG stored as a BinData part.
"""

from __future__ import annotations

import struct
import zlib

from lxml import etree

from hwp_agent.ops.figures import LoadedImage, add_bin_image, picture_xml


def tiny_png(w: int = 2, h: int = 2) -> bytes:
    raw = b"".join(b"\x00" + b"\x80\x80\xff" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def picture_run(doc, *, char_pr: str = "0", item_id: str | None = None):
    """An ``<hp:run>`` with one inline picture; stores a new image unless *item_id*."""
    img = LoadedImage(data=tiny_png(), ext="png", px_w=2, px_h=2)
    if item_id is None:
        item_id = add_bin_image(doc, img.data, img.ext)
    return etree.fromstring(picture_xml(
        item_id, img, 1000, 1000, char_pr=char_pr, source_name="tiny.png", label=None,
    ))
