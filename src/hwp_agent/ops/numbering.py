"""Keep decorative objects out of Hangul's 그림·표 차례 (#101).

Hangul's [차례 만들기] collects every object whose ``numberingType`` is ``PICTURE``
or ``TABLE`` — captioned or not.  A house form is full of uncaptioned decoration
(masterpage backgrounds, side-wing rectangles, divider lines, cover art), all
``PICTURE``, so a report with no figure at all gets a 27-entry figure list, and the
decorations also take figure numbers away from real figures.  Setting
``numberingType="NONE"`` on an object with no ``<hp:caption>`` removes it from the
list; objects ``write`` builds with a caption (figure tables, tables) are left alone.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from .container import _rewrite_zip_preserving

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
#: objects Hangul numbers (pictures, drawing objects, tables, equations)
_NUMBERED = {
    "pic", "rect", "line", "ellipse", "arc", "polygon", "curve", "connectLine",
    "textart", "ole", "container", "tbl", "equation",
}
_AUTO = {"PICTURE", "TABLE", "EQUATION"}


def _is_part(name: str) -> bool:
    return (
        name.startswith("Contents/section") or name.startswith("Contents/masterpage")
    ) and name.endswith(".xml")


def neutralize_uncaptioned_numbering(
    src: str | Path, dst: str | Path | None = None
) -> int:
    """Set ``numberingType="NONE"`` on every uncaptioned numbered object in *src*.

    Covers sections and masterpages (headers/footers live inside sections).  Only the
    parts that change are rewritten; the rest of the container is byte-identical.
    Returns how many objects changed; *dst* defaults to *src* (in place).
    """
    src = Path(src)
    dst = Path(dst) if dst is not None else src
    changed: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(src) as zf:
        for name in zf.namelist():
            if not _is_part(name):
                continue
            raw = zf.read(name)
            root = etree.fromstring(raw)
            n = 0
            for el in root.iter():
                if not isinstance(el.tag, str) or not el.tag.startswith("{" + _HP + "}"):
                    continue
                if el.tag.split("}")[1] not in _NUMBERED:
                    continue
                if el.get("numberingType") not in _AUTO:
                    continue
                if el.find(f"{{{_HP}}}caption") is not None:
                    continue  # a real, captioned figure/table
                el.set("numberingType", "NONE")
                n += 1
            if n:
                decl = raw[: raw.index(b"?>") + 2] if raw.lstrip().startswith(b"<?xml") else b""
                changed[name] = decl + etree.tostring(
                    root, encoding="UTF-8", xml_declaration=False
                )
                total += n
    if changed or dst != src:
        tmp = dst.with_suffix(dst.suffix + ".num.tmp")
        _rewrite_zip_preserving(src, tmp, changed)
        tmp.replace(dst)
    return total
