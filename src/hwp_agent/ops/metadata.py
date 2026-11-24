"""Read and fill HWPX document metadata (the cover/properties fields).

HWPX keeps document properties in ``Contents/content.hpf`` under
``<opf:metadata>`` — ``<opf:title>``/``<opf:language>`` elements plus a set of
``<opf:meta name="...">value</opf:meta>`` entries (creator, date, keyword, ...).
python-hwpx doesn't surface these, so we edit the part through its package API
(``get_xml`` / ``set_xml`` / ``save``), which keeps the rest of the document and
all namespaces intact.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC
from pathlib import Path

from hwpx.opc.package import HwpxPackage

_OPF = "http://www.idpf.org/2007/opf/"

#: field name -> (kind, xml-key). "elem" is an <opf:*> child element; "meta" is
#: an <opf:meta name="key"> entry.
_FIELD_MAP: dict[str, tuple[str, str]] = {
    "title": ("elem", "title"),
    "language": ("elem", "language"),
    "creator": ("meta", "creator"),
    "subject": ("meta", "subject"),
    "description": ("meta", "description"),
    "keyword": ("meta", "keyword"),
    "date": ("meta", "date"),
    "created": ("meta", "CreatedDate"),
    "modified": ("meta", "ModifiedDate"),
    "lastsaveby": ("meta", "lastsaveby"),
}


@dataclass
class Metadata:
    """Document properties carried in ``content.hpf``'s ``<opf:metadata>``."""

    title: str | None = None
    language: str | None = None
    creator: str | None = None
    subject: str | None = None
    description: str | None = None
    keyword: str | None = None
    date: str | None = None
    created: str | None = None
    modified: str | None = None
    lastsaveby: str | None = None

    def as_dict(self) -> dict[str, str]:
        """Non-empty fields only, in declaration order."""
        return {
            f.name: v
            for f in fields(self)
            if (v := getattr(self, f.name)) not in (None, "")
        }


def _metadata_element(pkg: HwpxPackage):
    path = pkg.main_content.full_path
    root = pkg.get_xml(path)
    md = root.find(f"{{{_OPF}}}metadata")
    if md is None:
        raise ValueError("content.hpf has no <opf:metadata> element")
    return path, root, md


def _read_value(md, kind: str, key: str) -> str | None:
    if kind == "elem":
        el = md.find(f"{{{_OPF}}}{key}")
    else:
        el = md.find(f'{{{_OPF}}}meta[@name="{key}"]')
    if el is None:
        return None
    return (el.text or "").strip() or None


def read_metadata(hwpx_path: Path | str) -> Metadata:
    """Read document metadata from an HWPX file."""
    pkg = HwpxPackage.open(str(hwpx_path))
    _, _, md = _metadata_element(pkg)
    return Metadata(
        **{name: _read_value(md, kind, key) for name, (kind, key) in _FIELD_MAP.items()}
    )


def update_metadata(
    hwpx_path: Path | str,
    *,
    output: Path | str | None = None,
    **values: str,
) -> tuple[str, ...]:
    """Set one or more metadata fields and save (in place unless ``output`` given).

    Pass any of :class:`Metadata`'s field names as keywords, e.g.
    ``update_metadata(p, title="...", creator="...")``. Missing ``<opf:meta>``
    entries are created. Returns the field names that were written.
    """
    unknown = set(values) - set(_FIELD_MAP)
    if unknown:
        raise ValueError(f"unknown metadata field(s): {', '.join(sorted(unknown))}")

    pkg = HwpxPackage.open(str(hwpx_path))
    path, root, md = _metadata_element(pkg)

    written: list[str] = []
    for name, value in values.items():
        kind, key = _FIELD_MAP[name]
        if kind == "elem":
            el = md.find(f"{{{_OPF}}}{key}")
            if el is None:
                el = md.makeelement(f"{{{_OPF}}}{key}", {})
                md.append(el)
        else:
            el = md.find(f'{{{_OPF}}}meta[@name="{key}"]')
            if el is None:
                el = md.makeelement(f"{{{_OPF}}}meta", {"name": key, "content": "text"})
                md.append(el)
        el.text = value
        written.append(name)

    pkg.set_xml(path, root)
    pkg.save(str(output or hwpx_path))
    return tuple(written)


#: document-info fields a template must not hand down to a written document: they
#: describe the template's own source file (its author, subject, DRM trace in
#: ``description``, save dates), not the new document (#120)
INHERITED_FIELDS: tuple[str, ...] = (
    "title",
    "creator",
    "subject",
    "description",
    "keyword",
    "date",
    "lastsaveby",
)


def _iso_now() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def reset_metadata_xml(md, *, title: str = "", stamp: str | None = None) -> None:
    """Blank :data:`INHERITED_FIELDS` in an ``<opf:metadata>`` element.

    ``title`` becomes *title*; ``CreatedDate``/``ModifiedDate`` become *stamp*
    (default: now, UTC). Entries that are absent are left absent.
    """
    stamp = stamp or _iso_now()
    for name in INHERITED_FIELDS:
        kind, key = _FIELD_MAP[name]
        if kind == "elem":
            el = md.find(f"{{{_OPF}}}{key}")
        else:
            el = md.find(f'{{{_OPF}}}meta[@name="{key}"]')
        if el is not None:
            el.text = title if name == "title" and title else None
    for name in ("created", "modified"):
        el = md.find(f'{{{_OPF}}}meta[@name="{_FIELD_MAP[name][1]}"]')
        if el is not None:
            el.text = stamp


def reset_package_metadata(pkg: HwpxPackage, *, title: str = "", stamp: str | None = None) -> None:
    """:func:`reset_metadata_xml` on an open package (``doc._package``); not saved."""
    path, root, md = _metadata_element(pkg)
    reset_metadata_xml(md, title=title, stamp=stamp)
    pkg.set_xml(path, root)


def _blank_png(w: int = 8, h: int = 8) -> bytes:
    """A white *w*x*h* PNG — the neutral ``Preview/PrvImage.png`` of a shipped template."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def neutralize_template(
    hwpx_path: Path | str,
    title: str,
    *,
    output: Path | str | None = None,
    stamp: str = "2026-01-01T00:00:00Z",
) -> None:
    """Strip a template's source-document traces from its package parts (#120).

    ``content.hpf``: ``title`` ← *title*, the other :data:`INHERITED_FIELDS`
    blanked, created/modified ← *stamp* (fixed so a rebuild is reproducible).
    ``Preview/PrvText.txt`` ← *title*, ``Preview/PrvImage.png`` ← a blank 8x8 PNG.
    Every other part is copied byte-for-byte (container order/compression kept).
    """
    import zipfile

    from .container import _rewrite_zip_preserving

    src = Path(hwpx_path)
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        hpf_name = "Contents/content.hpf"
        if hpf_name not in names:
            raise ValueError(f"{src}: no {hpf_name}")
        hpf = z.read(hpf_name).decode("utf-8")

    from lxml import etree

    decl_end = hpf.index("?>") + 2 if hpf.startswith("<?xml") else 0
    root = etree.fromstring(hpf[decl_end:].encode("utf-8"))
    md = root.find(f"{{{_OPF}}}metadata")
    if md is None:
        raise ValueError(f"{src}: content.hpf has no <opf:metadata>")
    reset_metadata_xml(md, title=title, stamp=stamp)
    body = etree.tostring(root, encoding="unicode")
    tail = hpf[len(hpf.rstrip()):]  # the trailing newline, if the file had one
    new_hpf = (hpf[:decl_end] + body + tail).encode("utf-8")

    overrides = {hpf_name: new_hpf}
    if "Preview/PrvText.txt" in names:
        overrides["Preview/PrvText.txt"] = title.encode("utf-8")
    if "Preview/PrvImage.png" in names:
        overrides["Preview/PrvImage.png"] = _blank_png()
    # always stage to a temp file: an *output* that aliases *src* would otherwise be
    # truncated before its parts are read
    dst = Path(output) if output else src
    tmp = dst.with_name(dst.name + ".neutral.tmp")
    _rewrite_zip_preserving(src, tmp, overrides)
    tmp.replace(dst)
