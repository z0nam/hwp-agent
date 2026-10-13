"""Package integrity audit: header id lists, dangling references, section secPr.

Issue #22 came from hand-edited packages, not from ``write``: a ``<hh:charPr>``
was inserted in the *middle* of the list (after id 11) with an id that already
existed (53). Hangul and rhwp resolve ``charPrIDRef`` by the element's
**position** in the list, so every run after the insertion silently took its
neighbour's character shape — ``BULLET_1`` (27) rendered at id 26's 7pt. The
same edit pass also replaced a section's paragraphs wholesale, dropping the one
that carried ``<hp:secPr>``; with no page definition rhwp lays out a zero-size
page and paginates into tens of thousands of pages.

Both are cheap to detect statically, so ``check`` reports them:

* **order** — a list whose ids are not contiguous in position order
  (an element inserted mid-list); consumers index by position, so later refs shift;
* **duplicate** — the same id twice in one list;
* **count** — ``itemCnt`` differs from the number of elements;
* **dangling** — a ``charPrIDRef`` / ``paraPrIDRef`` / ``styleIDRef`` /
  ``borderFillIDRef`` naming an id the header doesn't define;
* **no-secPr** — a section part without ``<hp:secPr>`` (no page size/margins).

Read-only; works on the raw package parts so it sees exactly what's on disk.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

#: header lists checked: (container tag, item tag, the reference attribute naming it)
_LISTS: tuple[tuple[str, str, str | None], ...] = (
    ("borderFills", "borderFill", "borderFillIDRef"),
    ("charProperties", "charPr", "charPrIDRef"),
    ("tabProperties", "tabPr", None),
    ("numberings", "numbering", None),
    ("bullets", "bullet", None),
    ("paraProperties", "paraPr", "paraPrIDRef"),
    ("styles", "style", "styleIDRef"),
)

_SECTION_RE = re.compile(r"^Contents/section\d+\.xml$")

#: Hangul's "no reference" sentinel (0xFFFFFFFF, i.e. -1 unsigned) — not dangling
_NONE_REF = "4294967295"


@dataclass
class IntegrityIssue:
    kind: str  # order | duplicate | count | dangling | no-secPr
    where: str  # e.g. "header charPr" or "Contents/section4.xml"
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


def _items(header: str, container: str, item: str) -> tuple[str | None, list[str]] | None:
    """(itemCnt, ids in document order) of one header list, or None if absent."""
    m = re.search(rf"<hh:{container}\b([^>]*)>(.*?)</hh:{container}>", header, re.S)
    if m is None:
        return None
    cnt = re.search(r'\bitemCnt="(\d+)"', m.group(1))
    # attribute order varies (python-hwpx appends id last), so match id anywhere
    tags = re.findall(rf"<hh:{item}\b[^>]*>", m.group(2))
    ids = [mm.group(1) for t in tags if (mm := re.search(r'\bid="([^"]*)"', t))]
    return (cnt.group(1) if cnt else None), ids


def audit_package(path: str | Path) -> list[IntegrityIssue]:
    """Return the integrity issues of the HWPX package at *path* (empty = clean)."""
    issues: list[IntegrityIssue] = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        header = zf.read("Contents/header.xml").decode("utf-8") if (
            "Contents/header.xml" in names
        ) else ""
        sections = sorted(
            (n for n in names if _SECTION_RE.match(n)),
            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)),
        )
        section_xml = {n: zf.read(n).decode("utf-8") for n in sections}
        # every other content part that can reference header ids — master pages
        # (page headers/footers/backgrounds), histories, … — not just the sections
        other_xml = {
            n: zf.read(n).decode("utf-8", "replace")
            for n in names
            if n.startswith("Contents/") and n.endswith(".xml")
            and n != "Contents/header.xml" and n not in section_xml
        }

    defined: dict[str, set[str]] = {}
    for container, item, ref_attr in _LISTS:
        got = _items(header, container, item)
        if got is None:
            continue
        item_cnt, ids = got
        where = f"header {item}"
        seen: set[str] = set()
        dups = sorted({i for i in ids if i in seen or seen.add(i)}, key=_num)
        if dups:
            issues.append(IntegrityIssue(
                "duplicate", where, f"id {', '.join(dups)} defined more than once"))
        nums = [_num(i) for i in ids]
        if ids and nums != list(range(nums[0], nums[0] + len(nums))):
            pos = next(k for k in range(1, len(nums)) if nums[k] != nums[k - 1] + 1)
            issues.append(IntegrityIssue(
                "order", where,
                f"ids not contiguous in list order — id {ids[pos]} sits at position "
                f"{pos} after id {ids[pos - 1]}; Hangul/rhwp index {item} by position, "
                "so every reference after it resolves to a neighbouring entry"))
        if item_cnt is not None and int(item_cnt) != len(ids):
            issues.append(IntegrityIssue(
                "count", where, f"itemCnt={item_cnt} but {len(ids)} element(s)"))
        if ref_attr:
            defined[ref_attr] = set(ids)

    # references to undefined ids, in every content part (sections, master pages, …)
    # and in the header's own styles
    scan = {**section_xml, **other_xml, "Contents/header.xml": header}
    for ref_attr, ids in defined.items():
        missing: dict[str, int] = {}
        for text in scan.values():
            for v in re.findall(rf'\b{ref_attr}="([^"]*)"', text):
                if v not in ids and v != _NONE_REF:
                    missing[v] = missing.get(v, 0) + 1
        if missing:
            listed = ", ".join(f"{k} (×{n})" for k, n in sorted(
                missing.items(), key=lambda kv: _num(kv[0])))
            issues.append(IntegrityIssue(
                "dangling", f"{ref_attr}", f"refers to undefined id {listed}"))

    for name, xml in section_xml.items():
        if "<hp:secPr" not in xml:
            issues.append(IntegrityIssue(
                "no-secPr", name,
                "section has no <hp:secPr> (page size/margins) — renderers lay out a "
                "zero-size page; restore the template section's first paragraph"))
    return issues


def _num(v: str) -> int:
    try:
        return int(v)
    except ValueError:
        return -1
