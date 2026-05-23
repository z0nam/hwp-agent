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
