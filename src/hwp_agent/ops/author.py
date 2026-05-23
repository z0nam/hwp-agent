"""Author content into a type-1 template by mapping Markdown to its styles.

The AI writes Markdown; we map each block to the template's own styles via
:func:`hwp_agent.ops.styles.role_map` and add paragraphs that reuse those style
ids — so outline numbering, fonts, and spacing come from the template (we never
synthesize numbering). Heading `#`→`HEADING_1`, `##`→`HEADING_2`, …; bullets →
`BULLET_n`; plain text → `BODY`.

Content is inserted at a ``{{body}}`` marker paragraph when present, else appended
to the last section. ``AI:INSTRUCTION``-styled paragraphs are read by
:func:`read_instructions` and stripped on fill. Inline ``**bold**``/``*italic*`` is
currently flattened to text (run-level styling is a later refinement).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

from hwpx.document import HwpxDocument

from .form import extract_placeholders
from .styles import INSTRUCTION, read_style_system, role_map

#: token that marks where the authored body is inserted (a paragraph of its own)
BODY_MARKER = "{{body}}"
#: a table whose caption carries a ``{{table…}}`` token (e.g. ``{{table}}``,
#: ``{{table_template}}``) is the format reference for generated tables.
_TABLE_TOKEN_RE = re.compile(r"\{\{\s*table[\w:-]*\s*\}\}", re.IGNORECASE)
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_ORDERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
# a table note/source line: 주) / 주: / <주> / 출처) / 자료: …
_NOTE_RE = re.compile(r"^\s*<?\s*(주|출처|자료)\s*[>)\].:]")
# caption placeholder for the current chapter number (cross-refs don't work in
# captions, so the toolkit substitutes the computed chapter number).
_CHAPTER_TOKEN_RE = re.compile(r"\{\{\s*chapter[\w]*\s*\}\}", re.IGNORECASE)


@dataclass
class Block:
    kind: str  # "heading" | "paragraph" | "bullet" | "ordered"
    level: int  # heading level 1-6, list nesting 1+, 0 for paragraph
    text: str  # raw inline text (** / * markers kept; segmented at fill time)


@dataclass
class Segment:
    text: str
    bold: bool = False
    italic: bool = False


@dataclass
class TableBlock:
    """A simple (rectangular) Markdown pipe table."""

    rows: list[list[str]]  # rows[0] is the header when has_header is True
    aligns: list[str]  # "left" | "center" | "right" per column
    has_header: bool = True
    caption: str | None = None  # title line directly above the table
    note: str | None = None  # 주)/출처) line(s) directly below the table

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass
class AuthorResult:
    placed: int = 0
    unmapped_roles: list[str] = field(default_factory=list)
    instructions_removed: int = 0
    inserted_at_marker: bool = False  # False = appended (no {{body}} marker found)

    def as_dict(self) -> dict:
        return {
            "placed": self.placed,
            "unmapped_roles": self.unmapped_roles,
            "instructions_removed": self.instructions_removed,
            "inserted_at_marker": self.inserted_at_marker,
        }


def inline_segments(text: str) -> list[Segment]:
    """Split inline ``**bold**`` / ``*italic*`` runs from plain text."""
    segments: list[Segment] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            segments.append(Segment(text[pos : m.start()]))
        if m.group(1) is not None:
            segments.append(Segment(m.group(1), bold=True))
        else:
            segments.append(Segment(m.group(2), italic=True))
        pos = m.end()
    if pos < len(text):
        segments.append(Segment(text[pos:]))
    return segments or [Segment(text)]


def plain_text(text: str) -> str:
    """Inline text with emphasis markers removed."""
    return "".join(s.text for s in inline_segments(text))


def _split_table_row(line: str) -> list[str]:
    """Split a pipe-table row into trimmed cells (outer pipes optional)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _delimiter_aligns(line: str) -> list[str] | None:
    """Return per-column alignments if *line* is a GFM table delimiter, else None."""
    cells = _split_table_row(line)
    aligns = []
    for c in cells:
        if not re.fullmatch(r":?-+:?", c):
            return None
        aligns.append(
            "center" if c.startswith(":") and c.endswith(":")
            else "right" if c.endswith(":")
            else "left"
        )
    return aligns or None


def parse_markdown(markdown: str) -> list[Block | TableBlock]:
    """Parse Markdown into headings, paragraphs, bullets, ordered lists, and tables.

    Inline ``**bold**`` / ``*italic*`` markers are kept in ``Block.text`` and
    resolved into runs at fill time via :func:`inline_segments`. Pipe tables
    (header row + ``|---|`` delimiter + body rows) become :class:`TableBlock`.
    """
    blocks: list[Block | TableBlock] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("paragraph", 0, " ".join(para)))
            para.clear()

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        # GFM pipe table: a header line with '|', then a delimiter row
        if (
            "|" in line
            and i + 1 < len(lines)
            and (aligns := _delimiter_aligns(lines[i + 1]))
        ):
            # caption = the line directly above the table (last buffered paragraph
            # line, with no blank line between); the rest of the buffer is flushed.
            caption = para.pop().strip() if para else None
            flush()
            header = _split_table_row(line)
            rows = [header]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            # note = consecutive 주)/출처)/자료) lines directly below the table
            note_lines = []
            while i < len(lines) and _NOTE_RE.match(lines[i]):
                note_lines.append(lines[i].strip())
                i += 1
            aligns += ["left"] * (len(header) - len(aligns))
            blocks.append(
                TableBlock(
                    rows=rows,
                    aligns=aligns[: len(header)],
                    caption=caption,
                    note="\n".join(note_lines) or None,
                )
            )
            continue
        if not line.strip():
            flush()
        elif m := _HEADING_RE.match(line):
            flush()
            blocks.append(Block("heading", len(m.group(1)), m.group(2).strip()))
        elif m := _ORDERED_RE.match(line):
            flush()
            level = len(m.group(1).expandtabs(2)) // 2 + 1
            blocks.append(Block("ordered", level, m.group(2).strip()))
        elif m := _BULLET_RE.match(line):
            flush()
            level = len(m.group(1).expandtabs(2)) // 2 + 1
            blocks.append(Block("bullet", level, m.group(2).strip()))
        else:
            para.append(line.strip())
        i += 1
    flush()
    return blocks


@dataclass
class Zone:
    """Per-row-band cell format: edge-aware border fills + the cell content style."""

    left_bf: str | None = None  # first-column cell border fill
    mid_bf: str | None = None  # interior cell border fill
    right_bf: str | None = None  # last-column cell border fill
    style: str | None = None  # cell paragraph styleIDRef (e.g. JRI_표내용)
    para: str | None = None  # cell paragraph paraPrIDRef
    char: str | None = None  # cell run charPrIDRef
    height: str | None = None  # cellSz height (tight, like the template)
    vert_align: str | None = None  # subList vertAlign (e.g. TOP, so text hugs the top)
    margin: dict | None = None  # cellMargin attrs

    def border(self, col: int, ncols: int) -> str | None:
        if col == 0:
            return self.left_bf
        if col == ncols - 1:
            return self.right_bf
        return self.mid_bf


@dataclass
class TableFormat:
    """Format borrowed from a template table, applied to generated tables.

    Border fills are position-aware (left/interior/right columns) per row band —
    header, interior body, and the last body row (which carries the thick bottom).
    Content styles (styleIDRef / para / char) come from the reference's cells so a
    generated table reuses e.g. the JRI_표내용 style instead of falling back to Normal.
    """

    table_border_fill: str | None = None
    header: Zone = field(default_factory=Zone)
    first_body: Zone = field(default_factory=Zone)  # row under header (pairs its double rule)
    body: Zone = field(default_factory=Zone)  # interior body rows (thin top/bottom)
    last: Zone = field(default_factory=Zone)  # last body row (thick bottom)
    note: Zone | None = None  # trailing note/source row (hidden borders)


def _cell_attrs(tc):
    sub = tc.find(f"{{{_HP}}}subList")
    p = sub.find(f"{{{_HP}}}p") if sub is not None else None
    run = p.find(f"{{{_HP}}}run") if p is not None else None
    return (
        tc.get("borderFillIDRef"),
        p.get("styleIDRef") if p is not None else None,
        p.get("paraPrIDRef") if p is not None else None,
        run.get("charPrIDRef") if run is not None else None,
    )


def _zone(tr) -> Zone:
    """Read a Zone from a row: edge border fills, content style, and geometry."""
    cells = tr.findall(f"{{{_HP}}}tc")
    if not cells:
        return Zone()
    left_bf, style, para, char = _cell_attrs(cells[0])
    mid_bf = _cell_attrs(cells[1])[0] if len(cells) > 2 else left_bf
    right_bf = _cell_attrs(cells[-1])[0]
    # geometry (tight height + alignment + margins) from the representative cell
    sz = cells[0].find(f"{{{_HP}}}cellSz")
    sub = cells[0].find(f"{{{_HP}}}subList")
    mg = cells[0].find(f"{{{_HP}}}cellMargin")
    return Zone(
        left_bf, mid_bf, right_bf, style, para, char,
        height=sz.get("height") if sz is not None else None,
        vert_align=sub.get("vertAlign") if sub is not None else None,
        margin=dict(mg.attrib) if mg is not None else None,
    )


def _is_header_row(tr) -> bool:
    """A header row = its first cell carries header="1" (handles complex headers)."""
    cells = tr.findall(f"{{{_HP}}}tc")
    return bool(cells) and cells[0].get("header") == "1"


def _table_format(tbl) -> TableFormat:
    """Read header / body / last-row zones from a reference table."""
    rows = tbl.findall(f"{{{_HP}}}tr")
    fmt = TableFormat(table_border_fill=tbl.get("borderFillIDRef"))
    if not rows:
        return fmt

    # header band = leading rows whose cells are marked header="1" (more explicit
    # than "row 0"; headers are sometimes multi-row). Only the band's *bottom* rule
    # matters for the separator, so use the last header row's zone.
    h = 0
    while h < len(rows) and _is_header_row(rows[h]):
        h += 1
    h = h or 1  # fall back to one header row if none are marked
    fmt.header = _zone(rows[h - 1])
    header_style = fmt.header.style
    rows = [rows[h - 1], *rows[h:]]  # keep the header zone row first for body scan
    # body rows share the header's content style; a trailing row with a *different*
    # style (e.g. JRI_각주 note row) is the note row, excluded so the thick bottom
    # comes from the last real body row.
    body_rows, note_rows = [], []
    for tr in rows[1:]:
        z = _zone(tr)
        (note_rows if header_style and z.style not in (header_style, None) else body_rows).append(z)
    if body_rows:
        fmt.first_body = body_rows[0]  # top double rule pairs the header's bottom
        fmt.last = body_rows[-1]  # thick bottom
        # an interior row (neither first nor last) has thin top/bottom; fall back to
        # the first body row when the reference has too few rows to have an interior.
        fmt.body = body_rows[1] if len(body_rows) >= 3 else body_rows[0]
        # use the interior (tight) row height for all body rows — the reference's
        # first/last rows can be incidentally tall; borders still vary by band.
        fmt.first_body.height = fmt.last.height = fmt.body.height
    else:
        fmt.first_body = fmt.body = fmt.last = fmt.header
    if note_rows:
        fmt.note = note_rows[-1]
    return fmt


def _caption_text(tbl_element) -> str:
    cap = tbl_element.find(f"{{{_HP}}}caption")
    return "".join(t.text or "" for t in cap.iter(f"{{{_HP}}}t")) if cap is not None else ""


def _find_reference_table(doc: HwpxDocument):
    """Find the *live* table element whose format generated tables copy.

    A table whose **caption** carries a ``{{table…}}`` token is the designated
    reference (templates often have many decorative tables, so the author marks
    the standard one); otherwise the first table in the document is used. Returns
    ``(tbl_element, designated)`` — a live element from the section tree so edits
    (token stripping) persist on save.
    """
    first = first_section = None
    for section in doc.sections:
        for tbl in section.element.iter(f"{{{_HP}}}tbl"):
            if first is None:
                first, first_section = tbl, section
            if _TABLE_TOKEN_RE.search(_caption_text(tbl)):
                return tbl, section, True
    return first, first_section, False


def _strip_table_token(tbl_element) -> None:
    """Remove the ``{{table…}}`` designation token from a table's caption text."""
    cap = tbl_element.find(f"{{{_HP}}}caption")
    if cap is None:
        return
    for t in cap.iter(f"{{{_HP}}}t"):
        if t.text and _TABLE_TOKEN_RE.search(t.text):
            # remove only the token; keep caption framing like a closing ">"
            t.text = _TABLE_TOKEN_RE.sub("", t.text).rstrip()


def _clone_caption(ref_el, title: str, chapter: str | None):
    """Clone the reference table's caption (auto-number + 표제목 style) with *title*.

    The caption text is like "<표 {{chapter_number}}-" + autoNum + "> {title}". We
    substitute the chapter placeholder with *chapter* (the table auto-number stays an
    autoNum field), keep the framing, and set the title. Returns a detached caption
    element, or None when the reference has no caption.
    """
    cap = ref_el.find(f"{{{_HP}}}caption")
    if cap is None:
        return None
    clone = copy.deepcopy(cap)
    texts = clone.findall(f".//{{{_HP}}}t")
    for t in texts:  # cross-refs don't work in captions → substitute the chapter no.
        if t.text and _CHAPTER_TOKEN_RE.search(t.text):
            t.text = _CHAPTER_TOKEN_RE.sub(chapter or "", t.text)
    if texts:  # last text node holds "> {title-or-token}" → keep ">", set title
        framing = _TABLE_TOKEN_RE.sub("", texts[-1].text or "").rstrip()
        texts[-1].text = f"{framing} {title}".rstrip()
    return clone


def _style_cell(cell, zone: Zone, col: int, ncols: int, text: str) -> None:
    tc = cell.element
    if (bf := zone.border(col, ncols)) is not None:
        tc.set("borderFillIDRef", bf)
    # tight geometry from the template: short cell height + top alignment + margins
    # (add_table defaults are loose — height ~3600, vertAlign CENTER, margins 0).
    if zone.height is not None and (sz := tc.find(f"{{{_HP}}}cellSz")) is not None:
        sz.set("height", zone.height)
    sub = tc.find(f"{{{_HP}}}subList")
    if sub is not None and zone.vert_align is not None:
        sub.set("vertAlign", zone.vert_align)
    if zone.margin is not None and (mg := tc.find(f"{{{_HP}}}cellMargin")) is not None:
        for k, v in zone.margin.items():
            mg.set(k, v)
    cp = cell.paragraphs[0]
    if zone.style is not None:
        cp.style_id_ref = zone.style
    if zone.para is not None:
        cp.para_pr_id_ref = zone.para
    cp.clear_text()
    # cell text is plain (no inline-bold variant): ensure_run_style ignores the cell
    # base size and would blow up the font, so cells keep their content char style.
    cp.add_run(plain_text(text), char_pr_id_ref=zone.char)


def _build_table(
    doc, section, block: TableBlock, fmt, body_style, body_para, ref_el, chapter=None
):
    """Create a table sized to *block*, styled by *fmt*'s zones, and fill its cells.

    Each row band picks a zone (header / interior body / last body row), and within a
    row the border fill is chosen by column position (first / interior / last) so the
    template's edge rules (no outer left/right line, thick bottom) survive a resize.
    A trailing note/source row (merged, hidden borders) is appended when the reference
    has one. The wrapping paragraph uses the BODY style and the table copies the
    reference's position (so it isn't anchored as a heading character).
    """
    ncols = block.n_cols
    data_rows = block.rows
    has_note = fmt is not None and fmt.note is not None
    total_rows = len(data_rows) + (1 if has_note else 0)
    table = doc.add_table(
        total_rows,
        ncols,
        section=section,
        border_fill_id_ref=(fmt.table_border_fill if fmt else None),
        style_id_ref=body_style,  # wrapping paragraph = body, not the inherited heading
        para_pr_id_ref=body_para,
    )
    # match the reference table's anchoring (floating, repeats header across pages)
    if ref_el is not None:
        gen_pos = table.element.find(f"{{{_HP}}}pos")
        ref_pos = ref_el.find(f"{{{_HP}}}pos")
        if gen_pos is not None and ref_pos is not None:
            for k, v in ref_pos.attrib.items():
                gen_pos.set(k, v)
        if ref_el.get("repeatHeader"):
            table.element.set("repeatHeader", ref_el.get("repeatHeader"))

    last_data = len(data_rows) - 1
    for r, row in enumerate(data_rows):
        is_header = block.has_header and r == 0
        if fmt is None:
            zone = Zone()
        elif is_header:
            zone = fmt.header
        elif r == last_data:
            zone = fmt.last  # last body row carries the thick bottom border
        elif r == 1 and block.has_header:
            zone = fmt.first_body  # pairs the header's double separator (no doubling)
        else:
            zone = fmt.body  # interior body row (thin top/bottom)
        for c in range(ncols):
            cell = table.cell(r, c)
            if is_header:
                cell.element.set("header", "1")  # repeat across page breaks
            _style_cell(cell, zone, c, ncols, row[c] if c < len(row) else "")

    if has_note:  # note/source row: one merged, hidden-bordered cell (kept if empty)
        note_r = total_rows - 1
        note_tr = table.element.findall(f"{{{_HP}}}tr")[note_r]
        tcs = note_tr.findall(f"{{{_HP}}}tc")
        if len(tcs) > 1:  # merge across all columns (merge_cells leaves covered cells)
            span = tcs[0].find(f"{{{_HP}}}cellSpan")
            if span is not None:
                span.set("colSpan", str(ncols))
            sz = tcs[0].find(f"{{{_HP}}}cellSz")
            widths = [tc.find(f"{{{_HP}}}cellSz") for tc in tcs]
            total_w = sum(int(s.get("width", "0")) for s in widths if s is not None)
            if sz is not None and total_w:
                sz.set("width", str(total_w))
            for extra in tcs[1:]:
                note_tr.remove(extra)
        note_text = block.note.replace("\n", "  ") if block.note else ""
        _style_cell(table.cell(note_r, 0), fmt.note, 0, 1, note_text)

    # caption "<표 N-M> {title}" cloned from the reference (auto-number + 표제목 style)
    if block.caption and ref_el is not None:
        clone = _clone_caption(ref_el, block.caption, chapter)
        if clone is not None:
            first_tr = table.element.find(f"{{{_HP}}}tr")
            if first_tr is not None:
                first_tr.addprevious(clone)
            else:
                table.element.append(clone)
    return table


def _find_body_marker(doc: HwpxDocument):
    """Locate the top-level paragraph holding the ``{{body}}`` marker, if any."""
    for section in doc.sections:
        for paragraph in section.paragraphs:
            if BODY_MARKER in (paragraph.text or ""):
                return section, paragraph
    return None, None


def read_instructions(template: Path | str) -> dict:
    """Authoring directions a template carries: ``AI:INSTRUCTION`` text + slots."""
    roles = role_map(template)
    inst_style = roles.get(INSTRUCTION)
    doc = HwpxDocument.open(str(template))
    instructions = []
    if inst_style is not None:
        instructions = [
            p.text.strip()
            for p in doc.paragraphs
            if str(p.style_id_ref) == inst_style and p.text.strip()
        ]
    slots = extract_placeholders(doc.export_text())
    return {"instructions": instructions, "slots": slots}


def fill_from_markdown(
    template: Path | str,
    markdown: str,
    *,
    output: Path | str | None = None,
) -> AuthorResult:
    """Append Markdown content to a template, styled with its own outline styles."""
    roles = role_map(template)
    infos = {i.style_id: i for i in read_style_system(template)}
    def _levels(prefix: str) -> list[int]:
        return sorted(int(r.split("_")[1]) for r in roles if r.startswith(prefix))

    max_heading = max(_levels("HEADING_"), default=0)
    bullet_levels, ordered_levels = _levels("BULLET_"), _levels("ORDERED_")

    def _ranked(prefix: str, levels: list[int], depth: int) -> str:
        # list styles may not start at level 1; map by rank (shallowest md depth
        # -> shallowest available level), clamped to the deepest defined.
        return f"{prefix}{levels[min(depth - 1, len(levels) - 1)]}"

    def resolve(block: Block) -> tuple[str | None, str]:
        if block.kind == "heading" and max_heading:
            role = f"HEADING_{min(block.level, max_heading)}"
        elif block.kind == "ordered" and ordered_levels:
            role = _ranked("ORDERED_", ordered_levels, block.level)
        elif block.kind == "bullet" and bullet_levels:
            role = _ranked("BULLET_", bullet_levels, block.level)
        else:
            role = "BODY"
        return roles.get(role), role

    doc = HwpxDocument.open(str(template))
    result = AuthorResult()

    # strip AI:INSTRUCTION paragraphs (directions, not content)
    inst_style = roles.get(INSTRUCTION)
    if inst_style is not None:
        for p in [p for p in doc.paragraphs if str(p.style_id_ref) == inst_style]:
            p.remove()
            result.instructions_removed += 1

    # insert at the {{body}} marker if present, else append to the last section
    marker_section, marker = _find_body_marker(doc)
    target_section = marker_section or doc.sections[-1]
    result.inserted_at_marker = marker is not None

    # tables generated below copy a template table's format (house style)
    ref_element, ref_section, designated = _find_reference_table(doc)
    table_fmt = _table_format(ref_element) if ref_element is not None else None

    def place(element) -> None:
        """Move a freshly-built element before the {{body}} marker (else leave appended)."""
        if marker is not None:
            element.getparent().remove(element)
            marker.element.addprevious(element)

    def add_runs(para, text: str, base_char: str | None) -> None:
        for seg in inline_segments(text):
            if seg.bold or seg.italic:
                char = doc.ensure_run_style(
                    bold=seg.bold, italic=seg.italic, base_char_pr_id=base_char
                )
            else:
                char = base_char
            para.add_run(seg.text, char_pr_id_ref=char)

    body_style = roles.get("BODY")
    body_para = infos[body_style].para_pr_id if body_style in infos else None

    # current chapter number for table captions ({{chapter_number}}): count
    # outline level-0 paragraphs (chapters can use several level-0 styles, so count
    # by outline level, not just the HEADING_1 style) before the insertion point,
    # plus authored "# " headings as they are placed.
    chapter = 0
    for p in doc.paragraphs:
        if marker is not None and p.element is marker.element:
            break
        pp = doc.paragraph_property(p.para_pr_id_ref)
        if pp and pp.heading and pp.heading.type == "OUTLINE" and pp.heading.level == 0:
            chapter += 1

    for block in parse_markdown(markdown):
        if isinstance(block, TableBlock):
            table = _build_table(
                doc, target_section, block, table_fmt, body_style, body_para,
                ref_element, str(chapter) if chapter else None,
            )
            place(table.paragraph.element)
            result.placed += 1
            continue

        style_id, role = resolve(block)
        if role == "HEADING_1":
            chapter += 1
        if style_id is None:
            style_id = roles.get("BODY")
            if role not in result.unmapped_roles and role != "BODY":
                result.unmapped_roles.append(role)
        para_pr = infos[style_id].para_pr_id if style_id in infos else None
        base_char = doc.style(style_id).char_pr_id_ref if style_id is not None else None

        # build the paragraph empty, then add one run per inline segment so
        # **bold** / *italic* become real runs (bold uses the same font family's
        # bold weight via ensure_run_style); plain text is a single run.
        para = target_section.add_paragraph(
            "", style_id_ref=style_id, para_pr_id_ref=para_pr, include_run=False
        )
        add_runs(para, block.text, base_char)
        place(para.element)
        result.placed += 1

    # clean the {{table…}} designation token out of the reference table's caption
    if designated and ref_element is not None:
        _strip_table_token(ref_element)
        ref_section.mark_dirty()  # so the edited section is re-serialized on save

    if marker is not None:
        marker.element.getparent().remove(marker.element)

    doc.save_to_path(str(output or template))
    return result
