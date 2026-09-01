"""Form-fill engine: discover an HWPX form's fillable slots, then fill them.

This is the core of the project's goal — hand the toolkit a template document and
let an AI (or a saved profile) fill it to fit the form. Slot kinds:

* **placeholder** — a ``{{ name }}`` token in the body text. Filled by text
  replacement across runs (split-run tokens are healed first).
* **cell** — a table label cell whose adjacent cell is the fill target (the usual
  Korean institutional form: ``과제명 │ ____``). Addressable either by the human
  ``"<label> > right"`` path or the stable ``cell:<table>:<row>:<col>`` path.
* **checkbox** — a ``□``/``■`` toggle next to a label (``□ 동의함`` → ``■ 동의함``).
* **inline** — a tab-separated field where the value is the ``.tail`` of an
  ``<hp:tab>`` inside an ``<hp:t>`` (e.g. 보안각서 "소속  ____" lines).

Table navigation reuses python-hwpx's span-aware logical grid (so merged cells
and nested tables resolve correctly), then edits the located ``<hp:tc>`` lxml
element directly for true overwrite (SET) semantics. Edited sections are marked
dirty so ``HwpxDocument.save_to_path`` re-serializes them.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

import hwpx.tools.table_navigation as _tn
from hwpx.document import HwpxDocument
from hwpx.opc.package import HwpxPackage

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

CHECKBOX_OFF, CHECKBOX_ON = "□", "■"
_CHECKBOX_PAIRS = ((CHECKBOX_OFF, CHECKBOX_ON), ("☐", "☑"), ("◻", "◼"))
# Map a human direction onto python-hwpx's grid directions.
_DIR_NORM = {
    "right": "right",
    "left": "left",
    "below": "down",
    "down": "down",
    "above": "up",
    "up": "up",
}


def q(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


def _sub(parent, tag: str):
    """Create + append a child element on *parent*'s own library (lxml or ET)."""
    child = parent.makeelement(tag, {})
    parent.append(child)
    return child


@dataclass
class FormSlot:
    """One fillable position in a form."""

    name: str  # placeholder name, label-cell text, checkbox/inline label
    kind: str  # "placeholder" | "cell" | "checkbox" | "inline"
    locator: str  # fill key: "{{name}}" / "<label> > right" / "checkbox:.." / "tab:.."
    current: str = ""  # current target text ("" for an empty/template slot)
    table_index: int | None = None
    row: int | None = None
    col: int | None = None
    cell_path: str | None = None  # stable "cell:<table>:<row>:<col>" alternate

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
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "filled": self.filled,
            "missing": self.missing,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# Low-level text helpers (operate on a list of section root elements)
# --------------------------------------------------------------------------- #
def _t_text(t) -> str:
    """Visible text of one ``<hp:t>`` — its text plus every child's tail."""
    return (t.text or "") + "".join((c.tail or "") for c in t)


def _run_text(run) -> str:
    return "".join(_t_text(t) for t in run.findall(q("t")))


def _cell_text(tc) -> str:
    return "".join(t.text or "" for t in tc.iter(q("t"))).strip()


def _set_para_text(p, text: str) -> None:
    """Set *p*'s visible text to *text*: first ``<hp:t>`` gets it, the rest blank."""
    ts = [t for r in p.findall(q("run")) for t in r.findall(q("t"))]
    if ts:
        for child in list(ts[0]):
            ts[0].remove(child)
        ts[0].text = text
        for t in ts[1:]:
            for child in list(t):
                t.remove(child)
            t.text = ""
    else:
        runs = p.findall(q("run")) or [_sub(p, q("run"))]
        _sub(runs[0], q("t")).text = text


def _set_cell_text_overwrite(tc, value: str) -> bool:
    """True SET: write ``value`` into the cell, clearing prior text.

    Sets the first ``<hp:t>`` in the cell's first paragraph (creating one if the
    run is empty — the blank-template ``<hp:run charPrIDRef=.../>`` case), blanks
    every other ``<hp:t>`` in that paragraph, and drops trailing paragraphs.
    This replaces python-hwpx ``set_cell_text``'s append behaviour.

    Newlines in ``value`` become real paragraph breaks: each extra line is a
    clone of the first paragraph (same paraPr/charPr), not a soft line break
    (SHIFT+ENTER) inside one paragraph.
    """
    sub = tc.find(q("subList"))
    if sub is None:
        return False
    ps = sub.findall(q("p"))
    if not ps:
        return False
    for extra in ps[1:]:
        sub.remove(extra)
    p = ps[0]
    lines = value.split("\n")
    _set_para_text(p, lines[0])
    for line in lines[1:]:
        clone = copy.deepcopy(p)
        _set_para_text(clone, line)
        sub.append(clone)
    return True


def _toggle_checkbox(roots, label: str, *, on: bool = True) -> bool:
    """Flip a ``□``/``■`` glyph adjacent to ``label`` (either side). Idempotent."""
    label = label.strip()
    esc_label = re.escape(label)
    changed = False
    for root in roots:
        for t in root.iter(q("t")):
            txt = t.text or ""
            if not txt:
                continue
            for off, on_glyph in _CHECKBOX_PAIRS:
                src, dst = (off, on_glyph) if on else (on_glyph, off)
                esc_src = re.escape(src)
                # glyph then label, or label then glyph (tolerating ". " etc.)
                before = re.compile(esc_src + r"(\s*)" + esc_label)
                after = re.compile(esc_label + r"([^\w\n]*)" + esc_src)
                new = before.sub(lambda m, d=dst: d + m.group(1) + label, txt)
                if new == txt:
                    new = after.sub(lambda m, d=dst: label + m.group(1) + d, txt)
                if new != txt:
                    txt = new
                    changed = True
            t.text = txt
    return changed


def _resolve_tab_anchor(roots, anchor: str):
    anchor = anchor.strip()
    for root in roots:
        for t in root.iter(q("t")):
            if (t.text or "").strip().startswith(anchor) and t.find(q("tab")) is not None:
                return t
    return None


def _set_tab_tail(t_elem, value: str, *, occurrence: int = 0) -> bool:
    """Set the ``.tail`` of the *occurrence*-th ``<hp:tab>`` child (inline field)."""
    tabs = t_elem.findall(q("tab")) or list(t_elem.iter(q("tab")))
    if len(tabs) <= occurrence:
        return False
    tabs[occurrence].tail = value
    return True


def _heal_split_placeholders(roots) -> int:
    """Merge ``{{tokens}}`` split across runs into one ``<hp:t>`` per paragraph.

    Only acts on plain paragraphs (no ``<hp:tab>``/``<hp:lineBreak>`` runs) so
    structured layout is never collapsed.
    """
    healed = 0
    for root in roots:
        for p in root.iter(q("p")):
            runs = p.findall(q("run"))
            if len(runs) < 2:
                continue
            full = "".join(_run_text(r) for r in runs)
            if "{{" not in full or "}}" not in full:
                continue
            singles: set[str] = set()
            for r in runs:
                singles |= set(PLACEHOLDER_RE.findall(_run_text(r)))
            if not (set(PLACEHOLDER_RE.findall(full)) - singles):
                continue  # nothing split across runs
            if any(
                r.find(q("tab")) is not None or r.find(q("lineBreak")) is not None
                for r in runs
            ):
                continue  # structured paragraph — leave alone
            ft = runs[0].find(q("t"))
            if ft is None:
                ft = _sub(runs[0], q("t"))
            for child in list(ft):
                ft.remove(child)
            ft.text = full
            for r in runs[1:]:
                for t in r.findall(q("t")):
                    for child in list(t):
                        t.remove(child)
                    t.text = ""
            healed += 1
    return healed


def _replace_text_everywhere(roots, search: str, repl: str) -> int:
    n = 0
    for root in roots:
        for t in root.iter(q("t")):
            if t.text and search in t.text:
                t.text = t.text.replace(search, repl)
                n += 1
            for child in t:
                if child.tail and search in child.tail:
                    child.tail = child.tail.replace(search, repl)
                    n += 1
    return n


# --------------------------------------------------------------------------- #
# Table navigation via python-hwpx (span-aware, nested-safe)
# --------------------------------------------------------------------------- #
def _locate_label_cell(doc: HwpxDocument, label: str, direction: str):
    """Element of the cell next to a unique label cell, or None."""
    d = _DIR_NORM.get(direction.strip().lower())
    if d is None:
        return None
    tables = _tn._collect_document_tables(doc)
    try:
        cands = _tn._find_label_candidates(tables, label.strip())
    except ValueError:
        return None
    if len(cands) != 1:  # not found or ambiguous
        return None
    c = cands[0]
    pos = _tn._move(c.table, c.row, c.col, d)
    if pos is None:
        return None
    try:
        return c.table.cell(*pos).element
    except Exception:
        return None


def _rc(table) -> tuple[int, int]:
    """``(row_count, col_count)`` — tolerant of attribute vs. method forms."""
    rc, cc = table.row_count, table.column_count
    return (rc() if callable(rc) else rc), (cc() if callable(cc) else cc)


@dataclass
class GridCell:
    """One addressable table cell — fillable via its ``cell:<t>:<r>:<c>`` path."""

    cell_path: str
    table_index: int
    row: int
    col: int
    text: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def dump_grid(hwpx_path: Path | str) -> list[GridCell]:
    """Every table cell with its ``cell:`` path and current text.

    :func:`analyze_form` only surfaces label→*empty*-neighbour pairs, so a cell
    that already holds text is never reported as a slot. ``fill_form`` accepts a
    ``cell:<table>:<row>:<col>`` key for *any* cell, though, so this enumerates
    the addresses needed to overwrite pre-filled cells (checkbox rows, forms
    whose answer cells ship with placeholder text, repeated labels).
    """
    cells: list[GridCell] = []
    doc = HwpxDocument.open(str(hwpx_path))
    for it in _tn._collect_document_tables(doc):
        ti, table = it.table_index, it.table
        try:
            nrows, ncols = _rc(table)
        except Exception:
            continue
        for r in range(nrows):
            for c in range(ncols):
                try:
                    text = (table.cell(r, c).text or "").strip()
                except Exception:
                    continue
                cells.append(
                    GridCell(
                        cell_path=f"cell:{ti}:{r}:{c}",
                        table_index=ti,
                        row=r,
                        col=c,
                        text=text,
                    )
                )
    return cells


def _locate_cell_addr(doc: HwpxDocument, table_index: int, row: int, col: int):
    for it in _tn._collect_document_tables(doc):
        if it.table_index == table_index:
            try:
                return it.table.cell(row, col).element
            except Exception:
                return None
    return None


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def extract_placeholders(text: str) -> list[str]:
    """Unique ``{{name}}`` slot names in first-seen order."""
    seen: dict[str, None] = {}
    for m in PLACEHOLDER_RE.finditer(text):
        seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def _is_label_shaped(text: str) -> bool:
    """A plausible form label: short, not a sentence, not itself a value."""
    if not text or len(text) > 20:
        return False
    if text[-1] in ".?!":
        return False
    if re.fullmatch(r"[\d.,/\-\s]+", text):
        return False
    return True


def table_label_slots(
    section_xml: bytes, *, table_offset: int = 0, precise: bool = True
) -> list[FormSlot]:
    """Label cells (non-empty) whose right/below neighbour is empty (raw scan).

    A lightweight, package-layer discovery kept for unit tests and quick checks.
    :func:`analyze_form` uses python-hwpx's span-aware grid instead, which numbers
    tables differently; for fillable ``cell_path`` use the slots from
    :func:`analyze_form`.
    """
    root = ET.fromstring(section_xml)
    slots: list[FormSlot] = []
    for ti, tbl in enumerate(root.iter(q("tbl"))):
        grid: dict[tuple[int, int], str] = {}
        for tr in tbl.findall(q("tr")):  # direct cells only (skip nested tables)
            for tc in tr.findall(q("tc")):
                addr = tc.find(q("cellAddr"))
                if addr is None:
                    continue
                r, c = int(addr.get("rowAddr", "0")), int(addr.get("colAddr", "0"))
                grid[(r, c)] = _cell_text(tc)

        candidates = _label_candidates(grid)
        if precise:
            candidates = _filter_precise(candidates)
        for text, nb, direction in candidates:
            slots.append(
                FormSlot(
                    name=text,
                    kind="cell",
                    locator=f"{text} > {direction}",
                    table_index=table_offset + ti,
                    row=nb[0],
                    col=nb[1],
                    cell_path=f"cell:{table_offset + ti}:{nb[0]}:{nb[1]}",
                )
            )
    return slots


def _is_banner_row(grid: dict[tuple[int, int], str], r: int, text: str) -> bool:
    """A row dominated by one (merged) value — a title/section header, not a label."""
    row_cells = [v for (rr, _c), v in grid.items() if rr == r]
    return len(row_cells) >= 3 and row_cells.count(text) >= len(row_cells) - 1


def _label_candidates(
    grid: dict[tuple[int, int], str],
) -> list[tuple[str, tuple[int, int], str]]:
    out: list[tuple[str, tuple[int, int], str]] = []
    for (r, c), text in grid.items():
        if not text:
            continue
        for direction, nb in (("right", (r, c + 1)), ("below", (r + 1, c))):
            if nb in grid and not grid[nb]:
                # a full-width merged banner spawns spurious "below" targets
                if direction == "below" and _is_banner_row(grid, r, text):
                    continue
                out.append((text, nb, direction))
                break  # one target per label
    return out


def _filter_precise(
    candidates: list[tuple[str, tuple[int, int], str]],
) -> list[tuple[str, tuple[int, int], str]]:
    form_like = len(candidates) >= 2  # several label→blank pairs ⇒ a form
    return [
        cand
        for cand in candidates
        if _is_label_shaped(cand[0]) and (form_like or len(candidates) == 1)
    ]


def _label_cell_slots(doc: HwpxDocument, *, precise: bool = True) -> list[FormSlot]:
    """Label→empty-neighbour cell slots, numbered by python-hwpx's table grid."""
    slots: list[FormSlot] = []
    for it in _tn._collect_document_tables(doc):
        ti, table = it.table_index, it.table
        grid: dict[tuple[int, int], str] = {}
        try:
            nrows, ncols = _rc(table)
        except Exception:
            continue
        for r in range(nrows):
            for c in range(ncols):
                try:
                    grid[(r, c)] = (table.cell(r, c).text or "").strip()
                except Exception:
                    pass
        candidates = _label_candidates(grid)
        if precise:
            candidates = _filter_precise(candidates)
        for text, nb, direction in candidates:
            slots.append(
                FormSlot(
                    name=text,
                    kind="cell",
                    locator=f"{text} > {direction}",
                    table_index=ti,
                    row=nb[0],
                    col=nb[1],
                    cell_path=f"cell:{ti}:{nb[0]}:{nb[1]}",
                )
            )
    return slots


def _checkbox_slots(roots) -> list[FormSlot]:
    slots: list[FormSlot] = []
    seen: set[str] = set()
    for root in roots:
        for t in root.iter(q("t")):
            txt = t.text or ""
            for off, on_glyph in _CHECKBOX_PAIRS:
                for glyph, checked in ((off, False), (on_glyph, True)):
                    idx = txt.find(glyph)
                    if idx < 0:
                        continue
                    after = txt[idx + 1 :].strip()
                    before = txt[:idx].strip()
                    if after:  # "□ 동의함" — label follows the glyph
                        label = after.split()[0]
                    elif before:  # "…동의합니다. □" — label precedes the glyph
                        label = before.split()[-1].strip(".·,)：:")
                    else:
                        label = ""
                    if not label or label in seen:
                        continue
                    seen.add(label)
                    slots.append(
                        FormSlot(
                            name=label,
                            kind="checkbox",
                            locator=f"checkbox:{label}",
                            current="on" if checked else "off",
                        )
                    )
    return slots


def _inline_slots(roots) -> list[FormSlot]:
    slots: list[FormSlot] = []
    for root in roots:
        for t in root.iter(q("t")):
            if t.find(q("tab")) is None:
                continue
            label = (t.text or "").strip()
            if not label:
                continue
            tail = "".join((c.tail or "") for c in t).strip()
            slots.append(
                FormSlot(name=label, kind="inline", locator=f"tab:{label}", current=tail)
            )
    return slots


def _roots(doc: HwpxDocument):
    return [s.element for s in doc.sections]


def analyze_form(hwpx_path: Path | str, *, precise: bool = True) -> FormSpec:
    """Discover the fillable slots in an HWPX form."""
    slots: list[FormSlot] = []
    pkg = HwpxPackage.open(str(hwpx_path))
    seen_ph: set[str] = set()
    for part in pkg.section_paths():
        text = "".join(
            t.text or "" for t in ET.fromstring(pkg.read(part)).iter(q("t"))
        )
        for name in extract_placeholders(text):
            if name not in seen_ph:
                seen_ph.add(name)
                slots.append(
                    FormSlot(name=name, kind="placeholder", locator=f"{{{{{name}}}}}")
                )

    doc = HwpxDocument.open(str(hwpx_path))
    roots = _roots(doc)
    slots.extend(_label_cell_slots(doc, precise=precise))
    slots.extend(_checkbox_slots(roots))
    slots.extend(_inline_slots(roots))
    return FormSpec(slots=slots)


# --------------------------------------------------------------------------- #
# Fill
# --------------------------------------------------------------------------- #
def _truthy(value: str) -> bool:
    return str(value).strip().lower() not in ("off", "0", "false", "no", "")


def _apply_one(
    doc: HwpxDocument, roots, key: str, value: str, by_name: dict[str, FormSlot]
) -> bool:
    """Route a single mapping entry to the right edit. Returns success."""
    if key.startswith("cell:"):
        try:
            _, ti, row, col = key.split(":")
            tc = _locate_cell_addr(doc, int(ti), int(row), int(col))
        except ValueError:
            return False
        return _set_cell_text_overwrite(tc, value) if tc is not None else False

    if key.startswith("checkbox:"):
        return _toggle_checkbox(roots, key[len("checkbox:") :], on=_truthy(value))

    if key.startswith("tab:"):
        anchor, _, occ = key[len("tab:") :].partition(":")
        t = _resolve_tab_anchor(roots, anchor)
        if t is None:
            return False
        return _set_tab_tail(t, value, occurrence=int(occ) if occ else 0)

    if " > " in key:  # "<label> > right|below|left|above"
        label, _, direction = key.partition(" > ")
        tc = _locate_label_cell(doc, label, direction)
        return _set_cell_text_overwrite(tc, value) if tc is not None else False

    slot = by_name.get(key)
    if slot is not None and slot.kind == "placeholder":
        return _replace_text_everywhere(roots, slot.locator, value) > 0
    if slot is not None and slot.kind == "cell":
        tc = _locate_cell_addr(doc, slot.table_index, slot.row, slot.col)
        return _set_cell_text_overwrite(tc, value) if tc is not None else False
    if slot is not None and slot.kind == "checkbox":
        return _toggle_checkbox(roots, slot.name, on=_truthy(value))
    if slot is not None and slot.kind == "inline":
        t = _resolve_tab_anchor(roots, slot.name)
        return _set_tab_tail(t, value) if t is not None else False

    # last resort: treat the key as a placeholder token
    return _replace_text_everywhere(roots, f"{{{{{key}}}}}", value) > 0


def _release_table_flow(roots) -> int:
    """Let table content flow across pages: on every ``<hp:tbl>`` set
    ``pageBreak="CELL"`` and its own ``<hp:pos treatAsChar="0">``.

    A form saved from Hangul often ships tables as ``treatAsChar="1"`` +
    ``pageBreak="TABLE"``, which traps the table in one paragraph so long cell
    content is silently truncated at the page edge (issue #12). Returns the count
    of tables changed. This *does* alter the document's layout semantics, so it is
    opt-in (``allow_table_flow``), never automatic.
    """
    changed = 0
    for root in roots:
        for tbl in root.iter(q("tbl")):
            touched = False
            if tbl.get("pageBreak") not in (None, "CELL"):
                tbl.set("pageBreak", "CELL")
                touched = True
            pos = tbl.find(q("pos"))  # the table's own position (direct child)
            if pos is not None and pos.get("treatAsChar") == "1":
                pos.set("treatAsChar", "0")
                touched = True
            if touched:
                changed += 1
    return changed


def _trapped_table_count(roots) -> int:
    """Count tables that would trap overflowing content (treatAsChar='1')."""
    n = 0
    for root in roots:
        for tbl in root.iter(q("tbl")):
            pos = tbl.find(q("pos"))
            if pos is not None and pos.get("treatAsChar") == "1":
                n += 1
    return n


def fill_form(
    hwpx_path: Path | str,
    mapping: dict[str, str],
    *,
    output: Path | str | None = None,
    precise: bool = True,
    allow_table_flow: bool = False,
) -> FillResult:
    """Fill slots by name/path, then save (in place unless ``output`` is given).

    A key may be a slot name from :func:`analyze_form`, or an explicit target:

    * ``"<label> > right|below|left|above"`` — a cell next to a label (overwrite),
    * ``"cell:<table>:<row>:<col>"`` — a cell by address,
    * ``"checkbox:<label>"`` with value ``on``/``off``,
    * ``"tab:<anchor>[:occurrence]"`` — an inline tab-tail field,
    * a ``{{placeholder}}`` name.

    Unrecognized / non-matching keys are reported in ``missing``.
    """
    by_name = {s.name: s for s in analyze_form(hwpx_path, precise=precise).slots}

    doc = HwpxDocument.open(str(hwpx_path))
    roots = _roots(doc)
    _heal_split_placeholders(roots)

    result = FillResult()
    for key, value in mapping.items():
        ok = _apply_one(doc, roots, key, value, by_name)
        (result.filled if ok else result.missing).append(key)

    if allow_table_flow:
        n = _release_table_flow(roots)
        if n:
            result.warnings.append(
                f"released {n} table(s) to flow across pages "
                "(treatAsChar=0, pageBreak=CELL) — layout semantics changed"
            )
    else:
        # warn (coarse) when long content lands in a page-trapped table: it would
        # be silently truncated. Precise overflow needs real typesetting; a length
        # heuristic is enough to stop it happening unnoticed (issue #12).
        longest = max((len(v) for v in mapping.values()), default=0)
        if longest > 200 and _trapped_table_count(roots):
            result.warnings.append(
                "long content in a table set to '글자처럼 취급'(treatAsChar=1) may be "
                "truncated at the page edge without flowing. Pass --allow-table-flow "
                "to release it, or clear the table's 배치 in Hangul."
            )

    for sec in doc.sections:  # raw lxml edits don't set the dirty flag themselves
        sec.mark_dirty()
    doc.save_to_path(str(output or hwpx_path))
    return result
