"""Replace embedded figure images in an HWPX **in place**.

A figure is an `<hp:pic>` whose `<hc:img binaryItemIDRef="imageN">` points at a
`BinData/imageN.<ext>` part; `Contents/content.hpf` maps the id to that href. The
human caption (`[그림 III-NN] 제목`) lives in the **pic's own paragraph**, so a pic
and its caption are 1:1 (the list-of-figures section has captions but no pic, so it is
excluded automatically). Each pic also carries an `<hp:shapeComment>` alt-text with the
original filename and pixel size.

The replacement rules are verified against a real report (see the project memory
``hwpx-image-replace-mechanism``):

* **Byte-only swap, container preserved.** Only the target ``BinData`` entry's bytes
  (and, when geometry/format change, the section XML / ``content.hpf`` bytes) are
  rewritten; the ZIP is re-emitted entry-by-entry with the original ``ZipInfo``, order,
  and compression, ``mimetype`` first and ``STORED``. A full re-zip (what
  ``HwpxDocument.save_to_path`` does) trips Hangul's 보안경고.
* **Slot format = extension.** The media-type is often ``image/unknown``, so Hangul
  keys off the file extension — a PNG slot needs PNG bytes, a BMP slot needs BMP. To
  change format we also rewrite the ``content.hpf`` href + extension and rename the part.
* **Display extent / aspect.** ``<hp:sz>``/``<hp:orgSz>``/``<hp:imgRect>`` is the box
  Hangul scales the image into; ``--fit aspect`` keeps the width and recomputes the
  height so the new image is not stretched. Pixel↔HWPUNIT ≈ ×75 (@96 dpi).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HC = "http://www.hancom.co.kr/hwpml/2011/core"

#: HWPUNIT (1/7200 inch) per pixel at 96 dpi — used for imgDim / imgClip.
_PX_TO_HWPUNIT = 75

_SECTION_RE = re.compile(r"Contents/section\d+\.xml$")
_SUPPORTED = {"png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif"}


# --------------------------------------------------------------------------- #
# data carriers
# --------------------------------------------------------------------------- #
@dataclass
class PicInfo:
    """A figure image slot found in the document."""

    ref: str  # binaryItemIDRef, e.g. "image7"
    caption: str  # text of the pic's enclosing paragraph
    origname: str | None  # original filename from the alt-text, if any
    px_w: int | None  # original pixel width from the alt-text
    px_h: int | None
    slot_format: str  # BinData extension, lowercased (png/bmp/...)
    extent_w: int  # <hp:sz> display width (HWPUNIT)
    extent_h: int
    section: str  # section part name

    @property
    def extent_ratio(self) -> float | None:
        return round(self.extent_w / self.extent_h, 4) if self.extent_h else None

    def as_dict(self) -> dict:
        return {
            "ref": self.ref,
            "caption": self.caption,
            "origname": self.origname,
            "px_w": self.px_w,
            "px_h": self.px_h,
            "slot_format": self.slot_format,
            "extent_w": self.extent_w,
            "extent_h": self.extent_h,
            "extent_ratio": self.extent_ratio,
            "section": self.section,
        }


@dataclass
class ReplaceOutcome:
    selector: str  # what the caller asked for (ref or caption)
    image_path: str
    status: str  # replaced | unmatched | ambiguous | missing_file | bad_image | format_mismatch
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "selector": self.selector,
            "image": self.image_path,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class ImageReplaceResult:
    replaced: int = 0
    outcomes: list[ReplaceOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "replaced": self.replaced,
            "outcomes": [o.as_dict() for o in self.outcomes],
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# image inspection (Pillow-optional)
# --------------------------------------------------------------------------- #
def sniff_format(data: bytes) -> str | None:
    """Identify an image's format from its magic bytes (png/jpeg/gif/bmp/tiff)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    # width/height are the two big-endian uint32 after the 8-byte sig + len + "IHDR"
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    return w, h


def _bmp_size(data: bytes) -> tuple[int, int]:
    w = int.from_bytes(data[18:22], "little", signed=True)
    h = int.from_bytes(data[22:26], "little", signed=True)
    return abs(w), abs(h)


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF markers carry the frame dimensions; skip the rest by segment length
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h = int.from_bytes(data[i + 5 : i + 7], "big")
            w = int.from_bytes(data[i + 7 : i + 9], "big")
            return w, h
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = int.from_bytes(data[i + 2 : i + 4], "big")
        i += 2 + seg
    raise ValueError("no SOF marker in JPEG")


def read_image_size(data: bytes) -> tuple[int, int]:
    """Return ``(pixel_width, pixel_height)``; raise ``ValueError`` if undecodable.

    Tries Pillow first (covers every format); falls back to header parsing for
    PNG / JPEG / BMP so the common cases work with no extra dependency.
    """
    try:  # pragma: no cover - exercised only when Pillow is installed
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except ImportError:
        pass
    fmt = sniff_format(data)
    if fmt == "png":
        return _png_size(data)
    if fmt == "bmp":
        return _bmp_size(data)
    if fmt == "jpeg":
        return _jpeg_size(data)
    raise ValueError(f"cannot read image size (format={fmt!r})")


def parse_alt_text(comment: str) -> tuple[str | None, int | None, int | None]:
    """Pull ``(origname, px_w, px_h)`` from an `<hp:shapeComment>` alt-text string."""
    name = re.search(r"원본 그림의 이름:\s*(.+)", comment)
    size = re.search(r"가로\s*(\d+)\s*pixel.*?세로\s*(\d+)\s*pixel", comment, re.DOTALL)
    origname = name.group(1).strip() if name else None
    px_w = int(size.group(1)) if size else None
    px_h = int(size.group(2)) if size else None
    return origname, px_w, px_h


# --------------------------------------------------------------------------- #
# locating pics + captions (read-only, via ElementTree)
# --------------------------------------------------------------------------- #
def _para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(f"{{{_HP}}}t")).strip()


def list_images(path: str | Path) -> list[PicInfo]:
    """List every figure image slot in the document (those with an `<hc:img>` ref)."""
    from xml.etree import ElementTree as ET

    path = Path(path)
    with zipfile.ZipFile(path) as zf:
        hpf = _read_text(zf, "Contents/content.hpf")
        id_to_ext = _manifest_ext(hpf)
        sections = sorted(n for n in zf.namelist() if _SECTION_RE.search(n))
        pics: list[PicInfo] = []
        for sec in sections:
            root = ET.fromstring(zf.read(sec))
            parents = {c: p for p in root.iter() for c in p}
            for pic in root.iter(f"{{{_HP}}}pic"):
                img = pic.find(f"{{{_HC}}}img")
                ref = img.get("binaryItemIDRef") if img is not None else None
                if not ref:
                    continue
                # caption = text of the enclosing <hp:p>
                node, caption = pic, ""
                while node in parents:
                    node = parents[node]
                    if node.tag == f"{{{_HP}}}p":
                        caption = _para_text(node)
                        break
                comment = pic.find(f"{{{_HP}}}shapeComment")
                origname, px_w, px_h = parse_alt_text(
                    comment.text or "" if comment is not None else ""
                )
                sz = pic.find(f"{{{_HP}}}sz")
                ew = int(sz.get("width", "0")) if sz is not None else 0
                eh = int(sz.get("height", "0")) if sz is not None else 0
                pics.append(
                    PicInfo(
                        ref=ref,
                        caption=caption,
                        origname=origname,
                        px_w=px_w,
                        px_h=px_h,
                        slot_format=id_to_ext.get(ref, ""),
                        extent_w=ew,
                        extent_h=eh,
                        section=sec,
                    )
                )
    return pics


def _manifest_ext(hpf: str) -> dict[str, str]:
    """Map ``image id -> lowercased BinData extension`` from ``content.hpf``."""
    out: dict[str, str] = {}
    for m in re.finditer(r'<opf:item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"[^>]*>', hpf):
        ident, href = m.group(1), m.group(2)
        if "BinData/" in href:
            out[ident] = href.rsplit(".", 1)[-1].lower() if "." in href else ""
    return out


def _manifest_href(hpf: str, ref: str) -> str | None:
    m = re.search(rf'<opf:item\b[^>]*\bid="{re.escape(ref)}"[^>]*\bhref="([^"]+)"', hpf)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _match_by_caption(pics: list[PicInfo], caption: str) -> list[PicInfo]:
    want = _norm(caption)
    exact = [p for p in pics if _norm(p.caption) == want]
    if exact:
        return exact
    return [p for p in pics if want and want in _norm(p.caption)]


# --------------------------------------------------------------------------- #
# geometry editing (scoped string surgery on the section XML)
# --------------------------------------------------------------------------- #
def _pic_block_span(section_xml: str, ref: str) -> tuple[int, int] | None:
    """Return the ``[start, end)`` span of the `<hp:pic>…</hp:pic>` carrying *ref*."""
    anchor = section_xml.find(f'binaryItemIDRef="{ref}"')
    if anchor < 0:
        return None
    start = section_xml.rfind("<hp:pic", 0, anchor)
    end = section_xml.find("</hp:pic>", anchor)
    if start < 0 or end < 0:
        return None
    return start, end + len("</hp:pic>")


def _apply_fit_to_block(block: str, fit: str, px_w: int, px_h: int) -> str:
    """Rewrite the pic block's geometry for the new image. ``fit='none'`` is a no-op."""
    if fit == "none":
        return block

    sz = re.search(r'<hp:sz\b[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"', block)
    target_w = int(sz.group(1)) if sz else 0
    if not target_w:
        org = re.search(r'<hp:orgSz\b[^>]*\bwidth="(\d+)"', block)
        target_w = int(org.group(1)) if org else px_w * _PX_TO_HWPUNIT
    target_h = round(target_w * px_h / px_w)
    dim_w, dim_h = px_w * _PX_TO_HWPUNIT, px_h * _PX_TO_HWPUNIT

    def set_wh(tag: str, text: str, w: int, h: int) -> str:
        text = re.sub(
            rf'(<{tag}\b[^>]*\bwidth=")\d+(")', rf"\g<1>{w}\g<2>", text, count=1
        )
        return re.sub(
            rf'(<{tag}\b[^>]*\bheight=")\d+(")', rf"\g<1>{h}\g<2>", text, count=1
        )

    block = set_wh("hp:sz", block, target_w, target_h)
    block = set_wh("hp:orgSz", block, target_w, target_h)
    # imgRect corners: pt1=(w,0) pt2=(w,h) pt3=(0,h)
    block = re.sub(r'(<hc:pt1\b[^>]*\bx=")\d+(")', rf"\g<1>{target_w}\g<2>", block, count=1)
    block = re.sub(
        r'(<hc:pt2\b[^>]*\bx=")\d+("[^>]*\by=")\d+(")',
        rf"\g<1>{target_w}\g<2>{target_h}\g<3>",
        block,
        count=1,
    )
    block = re.sub(r'(<hc:pt3\b[^>]*\by=")\d+(")', rf"\g<1>{target_h}\g<2>", block, count=1)
    # imgClip = full new image, imgDim = native size
    block = re.sub(r'(<hp:imgClip\b[^>]*\bright=")\d+(")', rf"\g<1>{dim_w}\g<2>", block, count=1)
    block = re.sub(r'(<hp:imgClip\b[^>]*\bbottom=")\d+(")', rf"\g<1>{dim_h}\g<2>", block, count=1)
    block = set_wh_dim(block, dim_w, dim_h)
    return block


def set_wh_dim(block: str, dim_w: int, dim_h: int) -> str:
    block = re.sub(
        r'(<hp:imgDim\b[^>]*\bdimwidth=")\d+(")', rf"\g<1>{dim_w}\g<2>", block, count=1
    )
    return re.sub(
        r'(<hp:imgDim\b[^>]*\bdimheight=")\d+(")', rf"\g<1>{dim_h}\g<2>", block, count=1
    )


# --------------------------------------------------------------------------- #
# container-preserving zip rewrite
# --------------------------------------------------------------------------- #
def _read_text(zf: zipfile.ZipFile, name: str) -> str:
    return zf.read(name).decode("utf-8")


def _rewrite_zip_preserving(
    src: str | Path, dst: str | Path, overrides: dict[str, bytes]
) -> None:
    """Copy *src* to *dst* replacing only the named parts, keeping the container intact.

    Each entry is re-emitted with its original :class:`zipfile.ZipInfo` (order and
    compression preserved); ``mimetype`` stays first and ``STORED``. This is what keeps
    Hangul from treating the edited file as tampered (보안경고).
    """
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        with zipfile.ZipFile(dst, "w") as zout:
            for info in infos:
                data = overrides.get(info.filename)
                if data is None:
                    data = zin.read(info.filename)
                # reuse the source ZipInfo so order/compression/flags are preserved
                zout.writestr(info, data)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def replace_image(
    path: str | Path,
    *,
    ref: str | None = None,
    caption: str | None = None,
    image: str | Path,
    fit: str = "aspect",
    output: str | Path | None = None,
) -> ImageReplaceResult:
    """Replace one figure image in place, selected by ``ref`` or ``caption``.

    Exactly one of *ref* / *caption* must be given. ``fit`` is ``aspect`` (default,
    keep width and recompute height — undistorted) or ``none`` (leave the extent box
    untouched). The output is written to *output* (or in place) only when the
    replacement succeeds.
    """
    path = Path(path)
    image = Path(image)
    selector = ref or caption or ""
    result = ImageReplaceResult()

    if not image.is_file():
        result.outcomes.append(ReplaceOutcome(selector, str(image), "missing_file"))
        result.warnings.append(f"image file not found: {image}")
        return result

    img_bytes = image.read_bytes()
    try:
        px_w, px_h = read_image_size(img_bytes)
    except ValueError as exc:
        result.outcomes.append(ReplaceOutcome(selector, str(image), "bad_image", str(exc)))
        result.warnings.append(f"{image}: {exc}")
        return result
    new_fmt = sniff_format(img_bytes)

    pics = list_images(path)
    if ref:
        targets = [p for p in pics if p.ref == ref]
    else:
        targets = _match_by_caption(pics, caption or "")

    if not targets:
        result.outcomes.append(ReplaceOutcome(selector, str(image), "unmatched"))
        result.warnings.append(f"no figure matched {selector!r}")
        return result
    if len(targets) > 1:
        caps = "; ".join(f"{p.ref}:{p.caption!r}" for p in targets[:4])
        result.outcomes.append(ReplaceOutcome(selector, str(image), "ambiguous", caps))
        result.warnings.append(f"{selector!r} matched {len(targets)} figures: {caps}")
        return result

    target = targets[0]

    # slot-format guard: media-type is unreliable, so the extension must match.
    slot = target.slot_format
    if slot in ("jpg", "jpeg"):
        slot_norm = "jpeg"
    else:
        slot_norm = slot
    if new_fmt != slot_norm:
        detail = f"slot is .{slot}, image is {new_fmt}; re-encode to .{slot} or use a matching file"
        result.outcomes.append(
            ReplaceOutcome(selector, str(image), "format_mismatch", detail)
        )
        result.warnings.append(detail)
        return result

    with zipfile.ZipFile(path) as zf:
        hpf = _read_text(zf, "Contents/content.hpf")
        href = _manifest_href(hpf, target.ref)
        section_xml = _read_text(zf, target.section)

    overrides: dict[str, bytes] = {}
    if href:
        overrides[href] = img_bytes

    span = _pic_block_span(section_xml, target.ref)
    if span and fit != "none":
        block = section_xml[span[0] : span[1]]
        new_block = _apply_fit_to_block(block, fit, px_w, px_h)
        if new_block != block:
            section_xml = section_xml[: span[0]] + new_block + section_xml[span[1] :]
            overrides[target.section] = section_xml.encode("utf-8")

    out_path = Path(output) if output else path
    if out_path == path:
        # rewrite via a temp file, then swap, so we never read+write the same path
        tmp = path.with_name(path.name + ".tmp")
        _rewrite_zip_preserving(path, tmp, overrides)
        tmp.replace(path)
    else:
        _rewrite_zip_preserving(path, out_path, overrides)

    result.replaced = 1
    result.outcomes.append(
        ReplaceOutcome(
            selector,
            str(image),
            "replaced",
            f"{target.ref} <- {image.name} ({px_w}x{px_h}px, fit={fit})",
        )
    )
    return result
