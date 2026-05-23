"""Form-fill engine: discover an HWPX form's fillable slots, then fill them.

This is the core of the project's goal — hand the toolkit a template document and
let an AI fill it to fit the form. Two slot kinds are supported:

* **placeholder** — a ``{{ name }}`` token in the body text (an author marks
  where content goes). Filled with python-hwpx ``replace_text_in_runs``.
* **cell** — a table label cell whose adjacent cell is the fill target (the usual
  Korean institutional form: ``과제명 │ ____``). Discovery reports label cells
  with an empty right/below neighbour; filling uses python-hwpx ``fill_by_path``.

Discovery (:func:`analyze_form`) reads sections at the package layer (no full
document parse). Filling (:func:`fill_form`) uses the python-hwpx document API.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hwpx.document import HwpxDocument
from hwpx.opc.package import HwpxPackage

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


@dataclass
class FormSlot:
    """One fillable position in a form."""

    name: str  # placeholder name, or label-cell text
    kind: str  # "placeholder" | "cell"
    locator: str  # "{{name}}" or "<label> > <direction>" path for fill
    current: str = ""  # current target text ("" for an empty/template slot)
    table_index: int | None = None
    row: int | None = None
    col: int | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class FormSpec:
    """The fillable structure discovered in a form."""

    slots: list[FormSlot] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"slots": [s.as_dict() for s in self.slots]}

    def names(self) -> list[str]:
        return [s.name for s in self.slots]


@dataclass
class FillResult:
    filled: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"filled": self.filled, "missing": self.missing}


def _cell_text(tc: ET.Element) -> str:
    return "".join(t.text or "" for t in tc.iter(f"{{{_HP}}}t")).strip()


def extract_placeholders(text: str) -> list[str]:
    """Unique ``{{name}}`` slot names in first-seen order."""
    seen: dict[str, None] = {}
    for m in PLACEHOLDER_RE.finditer(text):
        seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def table_label_slots(section_xml: bytes, *, table_offset: int = 0) -> list[FormSlot]:
    """Label cells (non-empty) whose right or below neighbour is empty.

    Returns slots usable with ``fill_by_path`` ("<label> > right"/"> below").
    ``table_offset`` keeps ``table_index`` continuous across sections.
    """
    root = ET.fromstring(section_xml)
    slots: list[FormSlot] = []
    for ti, tbl in enumerate(root.iter(f"{{{_HP}}}tbl")):
        grid: dict[tuple[int, int], str] = {}
        for tc in tbl.iter(f"{{{_HP}}}tc"):
            addr = tc.find(f"{{{_HP}}}cellAddr")
            if addr is None:
                continue
            r, c = int(addr.get("rowAddr", "0")), int(addr.get("colAddr", "0"))
            grid[(r, c)] = _cell_text(tc)
        for (r, c), text in grid.items():
            if not text:
                continue
            for direction, nb in (("right", (r, c + 1)), ("below", (r + 1, c))):
                if nb in grid and not grid[nb]:
                    slots.append(
                        FormSlot(
                            name=text,
                            kind="cell",
                            locator=f"{text} > {direction}",
                            current="",
                            table_index=table_offset + ti,
                            row=nb[0],
                            col=nb[1],
                        )
                    )
                    break  # one target per label
    return slots


def analyze_form(hwpx_path: Path | str) -> FormSpec:
    """Discover the fillable slots in an HWPX form."""
    pkg = HwpxPackage.open(str(hwpx_path))
    slots: list[FormSlot] = []
    seen_ph: set[str] = set()
    table_offset = 0
    for part in pkg.section_paths():
        xml = pkg.read(part)
        text = "".join(
            t.text or "" for t in ET.fromstring(xml).iter(f"{{{_HP}}}t")
        )
        for name in extract_placeholders(text):
            if name not in seen_ph:
                seen_ph.add(name)
                slots.append(
                    FormSlot(name=name, kind="placeholder", locator=f"{{{{{name}}}}}")
                )
        section_slots = table_label_slots(xml, table_offset=table_offset)
        slots.extend(section_slots)
        table_offset += sum(1 for _ in ET.fromstring(xml).iter(f"{{{_HP}}}tbl"))
    return FormSpec(slots=slots)


def fill_form(
    hwpx_path: Path | str,
    mapping: dict[str, str],
    *,
    output: Path | str | None = None,
) -> FillResult:
    """Fill slots by name, then save (in place unless ``output`` is given).

    A key may be:

    * a slot name from :func:`analyze_form` (placeholder name or empty label cell),
    * an explicit cell path ``"<label> > right"`` / ``"> below"`` (filled with
      ``fill_by_path`` regardless of whether the slot was auto-discovered), or
    * a placeholder name not yet discovered (tried as ``{{name}}``).

    Unrecognized / non-matching keys are reported in ``missing`` rather than
    raising.
    """
    by_name = {s.name: s for s in analyze_form(hwpx_path).slots}

    doc = HwpxDocument.open(str(hwpx_path))
    result = FillResult()
    for key, value in mapping.items():
        slot = by_name.get(key)
        if slot is not None and slot.kind == "placeholder":
            ok = doc.replace_text_in_runs(slot.locator, value) > 0
        elif slot is not None:  # discovered empty label cell
            ok = doc.fill_by_path({slot.locator: value})["applied_count"] > 0
        elif " > " in key:  # explicit cell path
            ok = doc.fill_by_path({key: value})["applied_count"] > 0
        else:  # last resort: treat as a placeholder token
            ok = doc.replace_text_in_runs(f"{{{{{key}}}}}", value) > 0
        (result.filled if ok else result.missing).append(key)

    doc.save_to_path(str(output or hwpx_path))
    return result
