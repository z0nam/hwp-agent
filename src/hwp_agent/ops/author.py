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
            flush()
            header = _split_table_row(line)
            rows = [header]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            aligns += ["left"] * (len(header) - len(aligns))
            blocks.append(TableBlock(rows=rows, aligns=aligns[: len(header)]))
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
class TableFormat:
    """Format borrowed from a template table, applied to generated tables."""

    table_border_fill: str | None = None
    header_border_fill: str | None = None
    header_para_pr: str | None = None
    header_char_pr: str | None = None
    body_border_fill: str | None = None
    body_para_pr: str | None = None
    body_char_pr: str | None = None


def _cell_style(tc) -> tuple[str | None, str | None, str | None]:
    """(borderFillIDRef, paraPrIDRef, charPrIDRef) of a ``<hp:tc>``."""
    sub = tc.find(f"{{{_HP}}}subList")
    p = sub.find(f"{{{_HP}}}p") if sub is not None else None
    run = p.find(f"{{{_HP}}}run") if p is not None else None
    return (
        tc.get("borderFillIDRef"),
        p.get("paraPrIDRef") if p is not None else None,
        run.get("charPrIDRef") if run is not None else None,
    )


def _table_format(tbl) -> TableFormat:
    """Read a representative format (borders + header/body cell styles) from a table."""
    rows = tbl.findall(f"{{{_HP}}}tr")
    fmt = TableFormat(table_border_fill=tbl.get("borderFillIDRef"))
    if not rows:
        return fmt

    def first_cell(tr):
        cells = tr.findall(f"{{{_HP}}}tc")
        return cells[0] if cells else None

    fmt.header_border_fill, fmt.header_para_pr, fmt.header_char_pr = _cell_style(
        first_cell(rows[0])
    )
    body_row = rows[1] if len(rows) > 1 else rows[0]
    fmt.body_border_fill, fmt.body_para_pr, fmt.body_char_pr = _cell_style(
        first_cell(body_row)
    )
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
            t.text = re.sub(r"\s*>?\s*$", "", _TABLE_TOKEN_RE.sub("", t.text))


def _build_table(doc, section, block: TableBlock, fmt: TableFormat | None, add_runs):
    """Create a table sized to *block*, styled like *fmt*, and fill its cells."""
    table = doc.add_table(
        block.n_rows,
        block.n_cols,
        section=section,
        border_fill_id_ref=(fmt.table_border_fill if fmt else None),
    )
    for r, row in enumerate(block.rows):
        is_header = block.has_header and r == 0
        border = (fmt.header_border_fill if is_header else fmt.body_border_fill) if fmt else None
        para_pr = (fmt.header_para_pr if is_header else fmt.body_para_pr) if fmt else None
        char_pr = (fmt.header_char_pr if is_header else fmt.body_char_pr) if fmt else None
        for c in range(block.n_cols):
            cell = table.cell(r, c)
            if border:
                cell.element.set("borderFillIDRef", border)
            cp = cell.paragraphs[0]
            if para_pr:
                cp.para_pr_id_ref = para_pr
            cp.clear_text()
            add_runs(cp, row[c] if c < len(row) else "", char_pr)
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

    for block in parse_markdown(markdown):
        if isinstance(block, TableBlock):
            table = _build_table(doc, target_section, block, table_fmt, add_runs)
            place(table.paragraph.element)
            result.placed += 1
            continue

        style_id, role = resolve(block)
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
