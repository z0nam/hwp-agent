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
* **label** — a paragraph label followed by a value on the same line (e.g. ``□ 과 제 명 : ____``).
* **section** — empty paragraphs directly following a section label paragraph
  (e.g. ``□ 변경사유 :``).

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

from .tablewidth import compute_column_widths

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

CHECKBOX_OFF, CHECKBOX_ON = "□", "■"
_CHECKBOX_PAIRS = ((CHECKBOX_OFF, CHECKBOX_ON), ("☐", "☑"), ("◻", "◼"))
_SYMBOL_CHARS = r"□■○●\-\–\—ㆍ·\*"
_LABEL_LINE_RE = re.compile(
    r"^[\s]*["
    + _SYMBOL_CHARS
    + r"]?\s*(?P<raw_label>[^:：\n]+?)\s*[:：](?P<tail>[\s.~·\-년월일]*)$"
)
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
    kind: str  # "placeholder" | "cell" | "checkbox" | "inline" | "label" | "section"
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
    rows: list[str] = field(default_factory=list)  # row restructuring summary

    def as_dict(self) -> dict:
        return {
            "rows": self.rows,
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


def _nth_tbl(roots, table_index: int):
    """The ``table_index``-th ``<hp:tbl>`` in document order (matches dump_grid)."""
    idx = -1
    for root in roots:
        for t in root.iter(q("tbl")):
            idx += 1
            if idx == table_index:
                return t
    return None


def autofit_table(
    hwpx_path: Path | str,
    table_index: int = 0,
    *,
    min_widths: dict[int, int] | None = None,
    output: Path | str | None = None,
) -> dict:
    """Rebalance a table's column widths by content volume — no text is changed.

    Institutional forms ship tables with near-equal columns, but real content
    piles into one column, stretching the table vertically (issue #13). This
    redistributes the *same total width*: a per-column **min clamp** (longest
    word not cut) plus **√(content) damping** of the remainder, so a column with
    much more text gets more room without starving the label columns.
    ``min_widths`` (``{col: hwpunit}``) pins specific columns. Returns a summary.
    """
    doc = HwpxDocument.open(str(hwpx_path))
    roots = _roots(doc)
    tbl = _nth_tbl(roots, table_index)
    if tbl is None:
        raise ValueError(f"table index {table_index} not found")

    cells: list[tuple] = []  # (cellSz elem, colAddr, colSpan, textlen, longest_word)
    ncols = 0
    for tc in tbl.iter(q("tc")):
        addr, span, sz = tc.find(q("cellAddr")), tc.find(q("cellSpan")), tc.find(q("cellSz"))
        if addr is None or sz is None:
            continue
        c = int(addr.get("colAddr"))
        cs = int(span.get("colSpan")) if span is not None else 1
        text = "".join(e.text or "" for e in tc.iter(q("t")))
        lw = max((len(w) for w in text.split()), default=0)
        cells.append((sz, c, cs, len(text.strip()), lw))
        ncols = max(ncols, c + cs)
    if ncols == 0:
        raise ValueError("table has no addressable cells")

    col_w = [0] * ncols
    col_content = [0.0] * ncols
    longest = [0] * ncols
    span_w: dict[int, int] = {}  # fallback width for merged-only columns
    for sz, c, cs, tlen, lw in cells:
        w = int(sz.get("width"))
        if cs == 1:  # single-span cells define column geometry + min-clamp
            col_w[c] = w
            longest[c] = max(longest[c], lw)
        else:
            for cc in range(c, c + cs):
                span_w.setdefault(cc, w // cs)
        per = tlen / cs  # content in a merged cell spreads over the columns it covers
        for cc in range(c, c + cs):
            col_content[cc] += per
    for c in range(ncols):
        if col_w[c] == 0 and c in span_w:
            col_w[c] = span_w[c]
    total = sum(col_w)
    if total <= 0:
        raise ValueError("could not read column widths (merged-only columns?)")

    new = compute_column_widths(col_content, longest, total, min_widths)

    for sz, c, cs, _tlen, _lw in cells:
        sz.set("width", str(sum(new[c : c + cs])))

    for sec in doc.sections:
        sec.mark_dirty()
    doc.save_to_path(str(output or hwpx_path))
    return {
        "table_index": table_index,
        "ncols": ncols,
        "old_widths": col_w,
        "new_widths": new,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# Row restructuring (issue #43): delete / append / repeat-group
# --------------------------------------------------------------------------- #
class RowOpError(ValueError):
    """A row operation that would break the table (bad range, split merge)."""


@dataclass
class RowOp:
    """One structural row edit on table ``table`` (rows are 0-based, inclusive).

    * ``delete`` — remove rows ``start..end``.
    * ``repeat`` — insert ``count`` blank copies of rows ``start..end`` right
      after ``end``. ``--append-rows`` is a repeat of the last row
      (``start``/``end`` left ``None`` and resolved against the live table).
    """

    kind: str  # "delete" | "repeat"
    table: int
    start: int | None
    end: int | None
    count: int = 1
    source: str = ""  # the user's spec, for error messages

    def as_dict(self) -> dict:
        return asdict(self)


_ROW_SPEC_RE = re.compile(
    r"^cell:(?P<t>\d+):(?:(?P<s>\d+)(?:-(?P<e>\d+))?)?(?::?\+(?P<n>\d+))?$"
)


def parse_row_op(kind: str, spec: str) -> RowOp:
    """Parse a CLI row spec.

    * ``delete``: ``cell:T:S-E`` or ``cell:T:R``
    * ``append``: ``cell:T:+N``
    * ``repeat``: ``cell:T:S-E:+N`` (``cell:T:R:+N`` for one row)
    """
    m = _ROW_SPEC_RE.match(spec.strip().replace(" ", ""))
    usage = {
        "delete": "cell:<table>:<start>-<end>",
        "append": "cell:<table>:+<count>",
        "repeat": "cell:<table>:<start>-<end>:+<count>",
    }[kind]
    if m is None:
        raise RowOpError(f"{spec!r}: expected {usage}")
    t, s, e, n = m.group("t", "s", "e", "n")
    if kind == "append":
        if s is not None or n is None:
            raise RowOpError(f"{spec!r}: expected {usage}")
        return RowOp("repeat", int(t), None, None, int(n), spec)
    if s is None:
        raise RowOpError(f"{spec!r}: expected {usage}")
    start = int(s)
    end = int(e) if e is not None else start
    if end < start:
        raise RowOpError(f"{spec!r}: range end {end} is before start {start}")
    if kind == "delete":
        if n is not None:
            raise RowOpError(f"{spec!r}: expected {usage}")
        return RowOp("delete", int(t), start, end, 1, spec)
    if n is None:
        raise RowOpError(f"{spec!r}: expected {usage}")
    return RowOp("repeat", int(t), start, end, int(n), spec)


def parse_row_ops(
    delete_rows: list[str] | None = None,
    append_rows: list[str] | None = None,
    repeat_group: list[str] | None = None,
) -> list[RowOp]:
    """All row specs of one fill call — shared by the CLI, MCP and REST surfaces."""
    ops = [parse_row_op("delete", s) for s in delete_rows or []]
    ops += [parse_row_op("append", s) for s in append_rows or []]
    ops += [parse_row_op("repeat", s) for s in repeat_group or []]
    return ops


def parse_min_widths(items: list[str] | None) -> dict[int, int]:
    """``["COL=N", ...]`` → ``{col: hwpunit}`` for :func:`autofit_table`."""
    mins: dict[int, int] = {}
    for item in items or []:
        k, _, v = item.partition("=")
        try:
            mins[int(k)] = int(v)
        except ValueError:
            raise ValueError(f"min-width expects COL=N, got {item!r}") from None
    return mins


def _table_element(doc: HwpxDocument, table_index: int):
    """The live ``<hp:tbl>`` numbered like ``dump_grid`` / ``cell:`` paths."""
    for it in _tn._collect_document_tables(doc):
        if it.table_index == table_index:
            return it.table.element
    return None


def _cell_rows(tc) -> tuple[int, int]:
    """``(first_row, last_row)`` a cell covers."""
    addr, span = tc.find(q("cellAddr")), tc.find(q("cellSpan"))
    r0 = int(addr.get("rowAddr", "0"))
    rs = int(span.get("rowSpan", "1")) if span is not None else 1
    return r0, r0 + max(rs, 1) - 1


def _table_rows(tbl) -> list:
    """Direct ``<hp:tr>`` rows, checked to sit at ``rowAddr == index``."""
    trs = tbl.findall(q("tr"))
    for i, tr in enumerate(trs):
        for tc in tr.findall(q("tc")):
            if _cell_rows(tc)[0] != i:
                raise RowOpError(
                    f"row {i} holds a cell addressed to row {_cell_rows(tc)[0]} — "
                    "unexpected table layout, refusing to restructure"
                )
    return trs


def _check_range(tbl, start: int, end: int, op: RowOp) -> None:
    """Refuse when a vertical merge straddles the ``start..end`` boundary."""
    nrows = len(tbl.findall(q("tr")))
    if not (0 <= start <= end < nrows):
        raise RowOpError(
            f"{op.source}: rows {start}-{end} out of range (table {op.table} has "
            f"{nrows} rows: 0-{nrows - 1})"
        )
    for tr in tbl.findall(q("tr")):
        for tc in tr.findall(q("tc")):
            r0, r1 = _cell_rows(tc)
            if r1 < start or r0 > end or (start <= r0 and r1 <= end):
                continue  # outside, or fully inside the range
            col = tc.find(q("cellAddr")).get("colAddr", "?")
            raise RowOpError(
                f"{op.source}: a vertically merged cell (rows {r0}-{r1}, col {col}) "
                f"crosses the range boundary {start}-{end} — widen the range to "
                f"cover rows {min(r0, start)}-{max(r1, end)} or unmerge it in Hangul"
            )


def _row_heights(tbl) -> list[int]:
    """Per-row heights, solved from ``cellSz`` (merged cells fill the gaps)."""
    trs = tbl.findall(q("tr"))
    heights: list[int | None] = [None] * len(trs)
    cells = []
    for tr in trs:
        for tc in tr.findall(q("tc")):
            sz = tc.find(q("cellSz"))
            r0, r1 = _cell_rows(tc)
            cells.append((r0, r1, int(sz.get("height", "0")) if sz is not None else 0))
    for r0, r1, h in cells:  # single-row cells define their row
        if r0 == r1 and r0 < len(heights):
            heights[r0] = max(heights[r0] or 0, h)
    changed = True
    while changed:  # a merged cell with one unknown row pins that row
        changed = False
        for r0, r1, h in sorted(cells, key=lambda c: c[1] - c[0]):
            span = list(range(r0, min(r1, len(heights) - 1) + 1))
            unknown = [r for r in span if heights[r] is None]
            if len(unknown) == 1:
                known = sum(heights[r] or 0 for r in span if r not in unknown)
                heights[unknown[0]] = max(h - known, 0)
                changed = True
    # rows covered only by multi-row merges (e.g. one rowSpan=2 cell): split the
    # merge's remaining height evenly so the table height still tracks the rows
    for r0, r1, h in sorted(cells, key=lambda c: c[1] - c[0]):
        span = list(range(r0, min(r1, len(heights) - 1) + 1))
        unknown = [r for r in span if heights[r] is None]
        if unknown:
            known = sum(heights[r] or 0 for r in span if r not in unknown)
            for r in unknown:
                heights[r] = max(h - known, 0) // len(unknown)
    return [h or 0 for h in heights]


def _blank_cell(tc) -> None:
    """Empty a cloned cell's text, keeping every format reference.

    ``borderFillIDRef``/``cellSz``/``cellMargin`` and the first paragraph's
    ``paraPrIDRef``/``styleIDRef`` and run ``charPrIDRef`` stay as they were. Extra
    paragraphs go, inline objects (tabs, line breaks, controls) go, and the stale
    ``<hp:linesegarray>`` is dropped so Hangul re-lays the empty line (issue #15).
    """
    sub = tc.find(q("subList"))
    if sub is None:
        return
    ps = sub.findall(q("p"))
    for extra in ps[1:]:
        sub.remove(extra)
    if not ps:
        return
    p = ps[0]
    for lsa in p.findall(q("linesegarray")):
        p.remove(lsa)
    runs = p.findall(q("run"))
    for extra in runs[1:]:
        p.remove(extra)
    for run in runs[:1]:
        for child in list(run):
            run.remove(child)


def _shift_rows(trs, delta: int) -> None:
    for tr in trs:
        for tc in tr.findall(q("tc")):
            addr = tc.find(q("cellAddr"))
            addr.set("rowAddr", str(int(addr.get("rowAddr", "0")) + delta))


def _resize_table(tbl, delta_rows: int, delta_height: int) -> None:
    tbl.set("rowCnt", str(int(tbl.get("rowCnt", "0")) + delta_rows))
    sz = tbl.find(q("sz"))
    if sz is not None and sz.get("height") is not None:
        sz.set("height", str(max(int(sz.get("height")) + delta_height, 0)))


def _delete_rows(tbl, start: int, end: int) -> None:
    trs = _table_rows(tbl)
    heights = _row_heights(tbl)
    for tr in trs[start : end + 1]:
        tbl.remove(tr)
    n = end - start + 1
    _shift_rows(trs[end + 1 :], -n)
    _resize_table(tbl, -n, -sum(heights[start : end + 1]))


def _repeat_rows(tbl, start: int, end: int, count: int) -> None:
    trs = _table_rows(tbl)
    heights = _row_heights(tbl)
    n = end - start + 1
    _shift_rows(trs[end + 1 :], n * count)
    anchor = trs[end]
    for k in range(count):
        for tr in trs[start : end + 1]:
            clone = copy.deepcopy(tr)
            for tc in clone.findall(q("tc")):
                _blank_cell(tc)
            _shift_rows([clone], n * (k + 1))
            anchor.addnext(clone)
            anchor = clone
    _resize_table(tbl, n * count, sum(heights[start : end + 1]) * count)


def restructure_rows(doc: HwpxDocument, ops: list[RowOp]) -> list[str]:
    """Apply row ops in the fixed order **delete → append/repeat**.

    Deletes address the original table; appends/repeats address the table *after*
    the deletes. Within each phase ops run bottom-up so every spec keeps the
    coordinates the user wrote. Overlapping ranges in one phase are refused, as is
    any vertical merge that straddles a range boundary (fully-contained merges move
    with their rows). Raises :class:`RowOpError` on the first op it can't apply
    cleanly — callers then discard the document unsaved. Returns one summary line
    per op.
    """
    done: list[str] = []
    for kind in ("delete", "repeat"):
        phase = [op for op in ops if op.kind == kind]
        resolved: list[tuple[RowOp, object, int, int]] = []
        for op in phase:
            tbl = _table_element(doc, op.table)
            if tbl is None:
                raise RowOpError(f"{op.source}: table {op.table} not found")
            start, end = op.start, op.end
            if start is None:  # append: the last row
                start = end = len(tbl.findall(q("tr"))) - 1
            _table_rows(tbl)
            _check_range(tbl, start, end, op)
            resolved.append((op, tbl, start, end))
        by_table: dict[int, list[tuple[int, int, str]]] = {}
        for op, _tbl, s, e in resolved:
            for s2, e2, src in by_table.get(op.table, []):
                if s <= e2 and s2 <= e:
                    raise RowOpError(f"{op.source}: overlaps {src} (rows {s2}-{e2})")
            by_table.setdefault(op.table, []).append((s, e, op.source))
        for op, tbl, s, e in sorted(resolved, key=lambda r: -r[2]):
            if kind == "delete":
                _delete_rows(tbl, s, e)
                done.append(f"table {op.table}: deleted rows {s}-{e}")
            else:
                _repeat_rows(tbl, s, e, op.count)
                n = e - s + 1
                done.append(
                    f"table {op.table}: rows {s}-{e} ×{op.count} → new rows "
                    f"{e + 1}-{e + n * op.count}"
                )
    return done


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
                        if ":" in after or "：" in after:
                            colon_idx = (
                                after.find(":") if ":" in after else after.find("：")
                            )
                            tail = after[colon_idx + 1 :]
                            if re.fullmatch(r"[\s.~·\-년월일]*", tail):
                                continue
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


def _label_and_section_slots(roots) -> list[FormSlot]:
    slots: list[FormSlot] = []
    seen: set[str] = set()
    for root in roots:
        ps = root.findall(q("p"))
        n = len(ps)
        for i, p in enumerate(ps):
            if p.find(f".//{q('tbl')}") is not None:
                continue
            full_text = "".join(p.itertext()).strip()
            if not full_text:
                continue
            m = _LABEL_LINE_RE.match(full_text)
            if not m:
                continue
            raw_label = m.group("raw_label").strip()
            if not raw_label:
                continue
            tail = (m.group("tail") or "").strip()

            # If followed immediately by a table, it's a table caption/header,
            # not a section/label slot
            if i + 1 < n and ps[i + 1].find(f".//{q('tbl')}") is not None:
                continue

            # Count consecutive empty paragraphs
            empty_count = 0
            j = i + 1
            while j < n:
                pj = ps[j]
                if pj.find(f".//{q('tbl')}") is not None:
                    break
                if "".join(pj.itertext()).strip():
                    break
                empty_count += 1
                j += 1

            if empty_count >= 1:
                loc = f"section:{raw_label}"
                if loc not in seen:
                    seen.add(loc)
                    slots.append(
                        FormSlot(
                            name=raw_label,
                            kind="section",
                            locator=loc,
                            current="",
                        )
                    )
            else:
                loc = f"label:{raw_label}"
                if loc not in seen:
                    seen.add(loc)
                    slots.append(
                        FormSlot(
                            name=raw_label,
                            kind="label",
                            locator=loc,
                            current=tail,
                        )
                    )
    return slots


def _norm_slot_label(s: str) -> str:
    """Normalize label for matching by stripping whitespace."""
    return re.sub(r"\s+", "", s)


def _set_label_slot(roots, target_label: str, value: str) -> bool:
    target_norm = _norm_slot_label(target_label)
    for root in roots:
        ps = root.findall(q("p"))
        for p in ps:
            if p.find(f".//{q('tbl')}") is not None:
                continue
            full_text = "".join(p.itertext()).strip()
            m = _LABEL_LINE_RE.match(full_text)
            if not m:
                continue
            raw_label = m.group("raw_label").strip()
            if _norm_slot_label(raw_label) != target_norm:
                continue

            runs = p.findall(q("run"))
            colon_t = None
            colon_idx = -1
            target_run_idx = -1
            for r_idx, run in enumerate(runs):
                for t in run.findall(q("t")):
                    txt = t.text or ""
                    c = txt.find(":")
                    if c < 0:
                        c = txt.find("：")
                    if c >= 0:
                        colon_t = t
                        colon_idx = c
                        target_run_idx = r_idx
                        break
                if colon_t is not None:
                    break

            if colon_t is None:
                continue

            prefix = colon_t.text[: colon_idx + 1]
            colon_t.text = f"{prefix} {value}"

            for extra_run in runs[target_run_idx + 1 :]:
                p.remove(extra_run)

            for lsa in p.findall(q("linesegarray")):
                p.remove(lsa)

            return True
    return False


def _set_section_slot(roots, target_label: str, value: str) -> bool:
    target_norm = _norm_slot_label(target_label)
    for root in roots:
        ps = root.findall(q("p"))
        for i, p in enumerate(ps):
            if p.find(f".//{q('tbl')}") is not None:
                continue
            full_text = "".join(p.itertext()).strip()
            m = _LABEL_LINE_RE.match(full_text)
            if not m:
                continue
            raw_label = m.group("raw_label").strip()
            if _norm_slot_label(raw_label) != target_norm:
                continue

            empty_ps = []
            j = i + 1
            while j < len(ps):
                pj = ps[j]
                if pj.find(f".//{q('tbl')}") is not None:
                    break
                if "".join(pj.itertext()).strip():
                    break
                empty_ps.append(pj)
                j += 1

            if not empty_ps:
                return False

            template_p = empty_ps[0]
            first_run = template_p.find(q("run"))
            char_pr_id = (
                first_run.get("charPrIDRef") if first_run is not None else None
            )

            lines = value.splitlines() if value else [""]
            if not lines:
                lines = [""]

            new_ps = []
            for line in lines:
                np = copy.deepcopy(template_p)
                for lsa in np.findall(q("linesegarray")):
                    np.remove(lsa)
                runs = np.findall(q("run"))
                for r in runs[1:]:
                    np.remove(r)
                if runs:
                    r0 = runs[0]
                    for child in list(r0):
                        r0.remove(child)
                    t = _sub(r0, q("t"))
                    t.text = line
                else:
                    r0 = _sub(np, q("run"))
                    if char_pr_id is not None:
                        r0.set("charPrIDRef", char_pr_id)
                    t = _sub(r0, q("t"))
                    t.text = line
                new_ps.append(np)

            insert_idx = list(root).index(p) + 1
            for ep in empty_ps:
                root.remove(ep)
            for k, np in enumerate(new_ps):
                root.insert(insert_idx + k, np)

            return True
    return False


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
    slots.extend(_label_and_section_slots(roots))
    return FormSpec(slots=slots)


# --------------------------------------------------------------------------- #
# Fill
# --------------------------------------------------------------------------- #
def _truthy(value: str) -> bool:
    return str(value).strip().lower() not in ("off", "0", "false", "no", "")


def _apply_one(
    doc: HwpxDocument,
    roots,
    key: str,
    value: str,
    by_name: dict[str, FormSlot],
    *,
    restructured: bool = False,
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

    if key.startswith("label:"):
        return _set_label_slot(roots, key[len("label:") :], value)

    if key.startswith("section:"):
        return _set_section_slot(roots, key[len("section:") :], value)

    if " > " in key:  # "<label> > right|below|left|above"
        label, _, direction = key.partition(" > ")
        tc = _locate_label_cell(doc, label, direction)
        return _set_cell_text_overwrite(tc, value) if tc is not None else False

    slot = by_name.get(key)
    if slot is None:
        key_norm = _norm_slot_label(key)
        for s in by_name.values():
            if (
                _norm_slot_label(s.name) == key_norm
                or _norm_slot_label(s.locator) == key_norm
            ):
                slot = s
                break

    if slot is not None and slot.kind == "placeholder":
        return _replace_text_everywhere(roots, slot.locator, value) > 0
    if slot is not None and slot.kind == "cell":
        if restructured:  # rows moved since analysis: resolve the label live
            label, _, direction = slot.locator.partition(" > ")
            tc = _locate_label_cell(doc, label, direction)
        else:
            tc = _locate_cell_addr(doc, slot.table_index, slot.row, slot.col)
        return _set_cell_text_overwrite(tc, value) if tc is not None else False
    if slot is not None and slot.kind == "checkbox":
        return _toggle_checkbox(roots, slot.name, on=_truthy(value))
    if slot is not None and slot.kind == "inline":
        t = _resolve_tab_anchor(roots, slot.name)
        return _set_tab_tail(t, value) if t is not None else False
    if slot is not None and slot.kind == "label":
        return _set_label_slot(roots, slot.name, value)
    if slot is not None and slot.kind == "section":
        return _set_section_slot(roots, slot.name, value)

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
    row_ops: list[RowOp] | None = None,
) -> FillResult:
    """Fill slots by name/path, then save (in place unless ``output`` is given).

    A key may be a slot name from :func:`analyze_form`, or an explicit target:

    * ``"<label> > right|below|left|above"`` — a cell next to a label (overwrite),
    * ``"cell:<table>:<row>:<col>"`` — a cell by address,
    * ``"checkbox:<label>"`` with value ``on``/``off``,
    * ``"tab:<anchor>[:occurrence]"`` — an inline tab-tail field,
    * ``"label:<label>"`` — a paragraph label whose colon-tail value is overwritten,
    * ``"section:<label>"`` — multiline narrative filling empty paragraphs under a label,
    * a ``{{placeholder}}`` name.

    Unrecognized / non-matching keys are reported in ``missing``.

    ``row_ops`` (issue #43) restructure tables first — delete, then append/repeat
    (see :func:`restructure_rows`) — so ``cell:`` keys address the *new* rows. A
    bad row op raises :class:`RowOpError` and nothing is written.
    """
    by_name = {s.name: s for s in analyze_form(hwpx_path, precise=precise).slots}

    doc = HwpxDocument.open(str(hwpx_path))
    roots = _roots(doc)
    _heal_split_placeholders(roots)

    result = FillResult()
    if row_ops:
        result.rows = restructure_rows(doc, row_ops)
    for key, value in mapping.items():
        ok = _apply_one(doc, roots, key, value, by_name, restructured=bool(row_ops))
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
