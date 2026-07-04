"""Author content into a type-1 template by mapping Markdown to its styles.

The AI writes Markdown; we map each block to the template's own styles via
:func:`hwp_agent.ops.styles.role_map` and add paragraphs that reuse those style
ids — so outline numbering and fonts come from the template (we never synthesize
numbering). Heading `#`→`HEADING_1`, `##`→`HEADING_2`, …; bullets →
`BULLET_n`; plain text → `BODY`.

Headings get **contextual spacing**: one blank paragraph sized to the heading's
own level is inserted above it, except when it hugs its parent (a heading directly
under a shallower one, with no author blank line). A single fixed style margin
can't express this, so the gap is a real per-heading paragraph; an author blank
line forces a gap, and tightly-packed Markdown is filled by the structural rule.

Content is inserted at a ``{{body}}`` / ``{{appendix}}`` marker paragraph when
present (the marker is consumed on fill), else appended to the last section.
``AI:INSTRUCTION``-styled paragraphs are read by
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
from .styles import INSTRUCTION, bullet_glyph_name, read_style_system, role_map

#: tokens marking where authored content is inserted (each on a paragraph of its
#: own); the marker paragraph is consumed (removed) on fill. ``{{body}}`` = start of
#: the main body, ``{{appendix}}`` = start of an appendix.
BODY_MARKER = "{{body}}"
APPENDIX_MARKER = "{{appendix}}"
INSERTION_MARKERS = (BODY_MARKER, APPENDIX_MARKER)
#: a table whose caption carries a ``{{table…}}`` token (e.g. ``{{table}}``,
#: ``{{table_template}}``) is the format reference for generated tables.
_TABLE_TOKEN_RE = re.compile(r"\{\{\s*table[\w:-]*\s*\}\}", re.IGNORECASE)
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_HC = "http://www.hancom.co.kr/hwpml/2011/core"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
# a thematic break / horizontal rule: 3+ of -, *, or _ (optionally spaced), alone
# on a line. No pipes, so it never clashes with a `|---|` table delimiter.
_RULE_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
# manual section numbering at the START of a heading — stripped so the template's
# outline auto-numbering supplies it (we never synthesize numbers). Conservative:
# a bare integer is only numbering with a delimiter ("1." yes, "2024 결산" no), and
# a bare letter needs a sub-number ("A-1" yes, "A형" no), so real titles survive.
_HEADING_NUM_RE = re.compile(
    r"""^\s*(?:
        부록\s*[0-9A-Za-z가-힣]+            # 부록 A / 부록 1 / 부록 가
      | 제?\s*\d+\s*[편장절관항]              # 제1장 / 1장 / 2절
      | [0-9]+(?:\s*[.\-]\s*[0-9]+)+         # 1.1 / 1-2 / 1.1.1  (multi-level)
      | [0-9]+\s*[.)]                        # 1. / 1)            (delimited single)
      | [A-Za-z](?:\s*[.\-]\s*[0-9]+)+       # A-1 / A.1 / B-2
      | [IVXⅠ-Ⅻ]+\s*[.)]                     # Ⅱ. / III)          (roman, delimited)
    )\s*[.)\]:：\-]*\s+(?=\S)""",
    re.VERBOSE,
)
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_ORDERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
# a table note/source line: 주) / 주: / <주> / 출처) / 자료: …
_NOTE_RE = re.compile(r"^\s*<?\s*(주|출처|자료)\s*[>)\].:]")
# HTML comments are stripped from the source before parsing. Authors use
# `<!-- … -->` to comment-out drafts / TODOs / earlier sections; they shouldn't
# leak into the rendered output. Supports inline and block (multi-line) forms.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# caption placeholder for the chapter number (cross-refs don't work in captions).
# Optional inline value forces it: {{chapter_number=3}} / {{chapter = 가}} → group 1.
_CHAPTER_TOKEN_RE = re.compile(
    r"\{\{\s*chapter\w*\s*(?:=\s*([^}]*?))?\s*\}\}", re.IGNORECASE
)
# a leading caption framing the author may have typed into the Markdown
# (`<표 부록-1> 신뢰도 등급` / `[그림 Ⅰ-3] 추세`). The template's autonum supplies
# the framing on its own — typing it produces a doubled "<표 N> <표 N>" prefix,
# so we strip it. Both ASCII (< >) and fullwidth (〈 〉) angle brackets covered.
_CAPTION_FRAMING_RE = re.compile(
    r"^\s*(?:[<〈]\s*표\s*[^>〉]*[>〉]|\[\s*그림\s*[^\]]*\])\s*"
)
# LaTeX-style cross-references: {label:id} declares an id on the table whose
# caption it sits in (the token is stripped from the rendered caption); {ref:id}
# anywhere resolves to that table's autonum text (e.g. "표 부록-3"). Single-brace
# so they never clash with the template's double-brace tokens. v1 is static
# substitution at fill time — Hangul's own autonum still renders the caption, so
# the substitution assumes the "표 {chapter}-{N}" convention. See SKILL.md.
_LABEL_RE = re.compile(r"\{label:([^{}\s]+)\}")
_REF_RE = re.compile(r"\{ref:([^{}\s]+)\}")


@dataclass
class Block:
    kind: str  # "heading" | "paragraph" | "bullet" | "ordered" | "rule"
    level: int  # heading level 1-6, list nesting 1+, 0 for paragraph/rule
    text: str  # raw inline text (** / * markers kept; segmented at fill time)
    blank_before: int = 0  # blank lines that preceded this block in the Markdown
    raw_text: str = ""  # headings: title before number stripping (see Q: literal ladders)


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
    blank_before: int = 0  # blank lines that preceded this block in the Markdown

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
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "placed": self.placed,
            "unmapped_roles": self.unmapped_roles,
            "instructions_removed": self.instructions_removed,
            "inserted_at_marker": self.inserted_at_marker,
            "warnings": self.warnings,
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


def _resolve_cross_refs(
    blocks: list, chapter: str | None, warnings: list[str]
) -> None:
    """Resolve `{label:id}` / `{ref:id}` cross-references across the block list.

    Every :class:`TableBlock` is numbered in document order (1-based). A
    ``{label:id}`` in a table's caption registers that table's autonum text
    (``"표 {chapter}-{N}"``, or ``"표 N"`` if no chapter is set) under *id* and
    is stripped from the rendered caption; ``{ref:id}`` anywhere (prose, cells,
    captions, notes) is substituted with the looked-up text. This is **static**
    substitution at fill time — Hangul still autonumbers the caption itself, so
    we assume the standard "표 {chapter}-{N}" format. Duplicate labels and
    unresolved refs are surfaced as warnings; unresolved refs keep the token so
    the issue is visible in the output.
    """
    labels: dict[str, str] = {}
    table_seq = 0
    fmt = (lambda n: f"표 {chapter}-{n}") if chapter else (lambda n: f"표 {n}")
    for b in blocks:
        if isinstance(b, TableBlock) and b.caption:
            table_seq += 1
            for lid in _LABEL_RE.findall(b.caption):
                if lid in labels:
                    warnings.append(f"duplicate {{label:{lid}}}; first wins")
                else:
                    labels[lid] = fmt(table_seq)
            b.caption = _LABEL_RE.sub("", b.caption).strip()
        elif isinstance(b, TableBlock):
            table_seq += 1  # still numbered even without caption

    unresolved: list[str] = []

    def resolve(text: str | None) -> str | None:
        if not text or "{ref:" not in text:
            return text

        def sub(m: re.Match) -> str:
            lid = m.group(1)
            if lid in labels:
                return labels[lid]
            if lid not in unresolved:
                unresolved.append(lid)
            return m.group(0)

        return _REF_RE.sub(sub, text)

    for b in blocks:
        if isinstance(b, TableBlock):
            b.caption = resolve(b.caption)
            b.note = resolve(b.note)
            b.rows = [[resolve(c) or "" for c in r] for r in b.rows]
        else:
            b.text = resolve(b.text) or b.text
            if b.raw_text:
                b.raw_text = resolve(b.raw_text) or b.raw_text
    if unresolved:
        warnings.append(f"unresolved {{ref:…}}: {', '.join(unresolved)}")


def _strip_heading_number(text: str) -> str:
    """Drop manual section numbering from the front of a heading title.

    The template's heading styles auto-number (outline numbering), so a number
    typed into the Markdown ("## 부록 A: …", "### A-1 …", "## 1.1 …") would double
    up. We strip it, but never empty the heading — if nothing remains, keep the
    original (e.g. a bare "부록" or "1 서론" with no delimiter is left untouched).
    """
    stripped = _HEADING_NUM_RE.sub("", text, count=1).strip()
    return stripped or text


def _heading_render_text(block: Block, info) -> str:
    """Heading text to render when *block* lands on the style described by *info*.

    Outline styles auto-number, so the number-stripped title is used. A plain
    (non-OUTLINE) heading style — a normalized flat template's literal ladder,
    where Ⅰ./1./1) are typed text — has nothing to re-supply a stripped number,
    so the author's literal title is kept.
    """
    if block.kind != "heading" or not block.raw_text:
        return block.text
    if info is not None and info.heading_type == "OUTLINE":
        return block.text
    return block.raw_text


def _bullet_render_text(block: Block, role: str, info) -> str:
    """Bullet text to render when *block* lands on the style described by *info*.

    A BULLET-defined style renders its glyph itself. A plain style laddered as
    ``AI:BULLET_n`` (glyph-named manual bullet head — JI 관행) carries its marker
    as literal text, so the glyph from the style name is re-supplied here.
    """
    if block.kind != "bullet" or not role.startswith("BULLET_"):
        return block.text
    if info is None or info.heading_type != "NONE":
        return block.text
    glyph = bullet_glyph_name(info.name)
    return f"{glyph} {block.text}" if glyph else block.text


def parse_markdown(markdown: str) -> list[Block | TableBlock]:
    """Parse Markdown into headings, paragraphs, bullets, ordered lists, and tables.

    Inline ``**bold**`` / ``*italic*`` markers are kept in ``Block.text`` and
    resolved into runs at fill time via :func:`inline_segments`. Pipe tables
    (header row + ``|---|`` delimiter + body rows) become :class:`TableBlock`.
    """
    markdown = _HTML_COMMENT_RE.sub("", markdown)
    blocks: list[Block | TableBlock] = []
    para: list[str] = []
    pending_blanks = 0  # blank lines seen since the last emitted block

    def emit(block: Block | TableBlock) -> None:
        # stamp the run of blank lines that preceded this block, then consume it —
        # so a blank line before a heading attaches to that heading (see fill_from_
        # markdown's contextual spacing rule).
        nonlocal pending_blanks
        block.blank_before = pending_blanks
        pending_blanks = 0
        blocks.append(block)

    def flush() -> None:
        if para:
            emit(Block("paragraph", 0, " ".join(para)))
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
            # caption = the line directly above the table; allow at most one blank
            # line between caption and table (common academic-Markdown style).
            # Path 1: caption is the last line of the in-progress paragraph buffer.
            # Path 2: caption was already emitted as its own paragraph (one blank
            # line separated it from the table) — retract that paragraph.
            caption = None
            if para:
                caption = para.pop().strip()
            elif (
                blocks
                and isinstance(blocks[-1], Block)
                and blocks[-1].kind == "paragraph"
                and pending_blanks <= 1
            ):
                caption = blocks.pop().text.strip()
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
            emit(
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
            pending_blanks += 1
        elif _RULE_RE.match(line):
            flush()
            emit(Block("rule", 0, ""))
        elif m := _HEADING_RE.match(line):
            flush()
            title = m.group(2).strip()
            emit(
                Block(
                    "heading",
                    len(m.group(1)),
                    _strip_heading_number(title),
                    raw_text=title,
                )
            )
        elif m := _ORDERED_RE.match(line):
            flush()
            level = len(m.group(1).expandtabs(2)) // 2 + 1
            emit(Block("ordered", level, m.group(2).strip()))
        elif m := _BULLET_RE.match(line):
            flush()
            level = len(m.group(1).expandtabs(2)) // 2 + 1
            emit(Block("bullet", level, m.group(2).strip()))
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
    # body rows = every non-header row. A trailing note/source row (e.g. a JRI_각주
    # row) is the *trailing* row whose content style differs from the body style —
    # compared against the body style, NOT the header, because a template's header
    # and body cells frequently use different styles (e.g. 표제목 14 vs 표내용 8); the
    # old header-relative test misread every body row as a note row and collapsed the
    # whole table to the header look.
    body_rows = [_zone(tr) for tr in rows[h:]]
    note_rows = []
    body_style = next((z.style for z in body_rows if z.style), None)
    while len(body_rows) > 1 and body_style and body_rows[-1].style not in (body_style, None):
        note_rows.insert(0, body_rows.pop())
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


def _find_reference_table(doc: HwpxDocument, caption_pattern: re.Pattern | None = None):
    """Find the *live* table element whose format generated tables copy.

    A table whose **caption** carries a ``{{table…}}`` token is the designated
    reference (templates often have many decorative tables, so the author marks
    the standard one); a ``caption_pattern`` (from ``--table-template``) matches a
    caption by text instead, so the reference survives a prior ``author`` run that
    consumed the token. Otherwise the first table in the document is used. Returns
    ``(tbl_element, section, designated)`` — a live element from the section tree
    so edits (token stripping) persist on save.
    """
    first = first_section = None
    for section in doc.sections:
        for tbl in section.element.iter(f"{{{_HP}}}tbl"):
            if first is None:
                first, first_section = tbl, section
            caption = _caption_text(tbl)
            if _TABLE_TOKEN_RE.search(caption) or (
                caption_pattern is not None and caption_pattern.search(caption)
            ):
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

    If the author typed the framing into the Markdown title (`<표 부록-1> 신뢰도
    등급`), the cloned framing would double it — so a leading `<표 …>` / `[그림 …]`
    on the title is stripped before substitution.
    """
    if title:
        title = _CAPTION_FRAMING_RE.sub("", title, count=1).lstrip()
    cap = ref_el.find(f"{{{_HP}}}caption")
    if cap is None:
        return None
    clone = copy.deepcopy(cap)
    texts = clone.findall(f".//{{{_HP}}}t")

    # chapter, in precedence order:
    #   {{chapter_number=3}} inline value (forced) > --chapter > "" ;
    # if no {{chapter…}} token but an explicit chapter is given, replace the chapter
    # token in a "표 X-" framing (X precedes the table auto-number): "[표 I-" -> "[표 Ⅲ-".
    has_token = any(t.text and _CHAPTER_TOKEN_RE.search(t.text) for t in texts)
    if has_token:
        def _chapter_sub(m: re.Match[str]) -> str:
            inline = m.group(1)
            return inline.strip() if inline is not None else (chapter or "")

        for t in texts:
            if t.text:
                t.text = _CHAPTER_TOKEN_RE.sub(_chapter_sub, t.text)
    elif chapter:
        for t in texts:
            if t.text and re.search(r"표\s*\S+\s*-\s*$", t.text):
                t.text = re.sub(r"(표\s*)\S+(\s*-\s*)$", rf"\g<1>{chapter}\g<2>", t.text)
                break

    # title: the last text node holds "framing + {old title / token}"; keep the
    # framing up to the first ">" or "]" and replace the rest with the new title.
    if texts:
        last = texts[-1].text or ""
        m = re.match(r"^([^\]>]*[>\]])", last)
        framing = m.group(1) if m else _TABLE_TOKEN_RE.sub("", last).rstrip()
        texts[-1].text = f"{framing} {title}".strip()
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


def _text_width(section) -> int | None:
    """Text-column width = page width − left/right margins (HWPUNIT), from secPr."""
    page = section.element.find(f".//{{{_HP}}}pagePr")
    if page is None or not page.get("width"):
        return None
    margin = page.find(f"{{{_HP}}}margin")
    left = int(margin.get("left", "0")) if margin is not None else 0
    right = int(margin.get("right", "0")) if margin is not None else 0
    width = int(page.get("width")) - left - right
    return width if width > 0 else None


def _fit_table_width(table_el, text_width: int) -> None:
    """Scale every cell width so each row fills *text_width*, preserving column ratios.

    ``add_table`` gives tables arbitrary absolute widths — a wide table overflows the
    text column, a narrow one falls short. Scale each ``<hp:cellSz width>`` by
    ``text_width / row_total`` and set the table's own ``<hp:sz width>``; rounding
    drift is absorbed into the last cell of each row so the row sums exactly.
    """
    sz = table_el.find(f"{{{_HP}}}sz")
    if sz is not None:
        sz.set("width", str(text_width))
    for tr in table_el.findall(f"{{{_HP}}}tr"):
        cell_szs = [tc.find(f"{{{_HP}}}cellSz") for tc in tr.findall(f"{{{_HP}}}tc")]
        cell_szs = [c for c in cell_szs if c is not None]
        if not cell_szs:
            continue
        total = sum(int(c.get("width", "0")) for c in cell_szs)
        if total <= 0:
            continue
        running = 0
        for c in cell_szs[:-1]:
            scaled = round(int(c.get("width", "0")) * text_width / total)
            c.set("width", str(scaled))
            running += scaled
        cell_szs[-1].set("width", str(text_width - running))  # drift → last cell


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

    # fit the table to the text column (expand small tables, clamp wide ones) before
    # styling, so a later note-row merge sums the already-fitted cell widths
    tw = _text_width(section)
    if tw:
        _fit_table_width(table.element, tw)

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


def _lineseg_index(doc: HwpxDocument) -> dict[str, object]:
    """Map styleIDRef → a deep-copied ``<hp:linesegarray>`` from a real paragraph.

    Hangul demotes an outline heading to body when its paragraph lacks the
    ``<hp:linesegarray>`` (line-layout cache with the outline flags) that genuine
    headings carry — so authored headings need one cloned from a same-style heading.
    """
    index: dict[str, object] = {}
    for p in doc.paragraphs:
        sid = p.style_id_ref
        if sid is None or str(sid) in index:
            continue
        lsa = p.element.find(f"{{{_HP}}}linesegarray")
        if lsa is not None:
            index[str(sid)] = copy.deepcopy(lsa)
    return index


def _emphasis_char(doc: HwpxDocument, base_char: str | None, *, bold: bool, italic: bool):
    """A bold/italic charPr that **preserves the base char's size**.

    ``doc.ensure_run_style`` matches any existing charPr with the right flags,
    ignoring size — so inline ``**bold**`` in 9pt body text can pick up a 20pt
    chapter-title charPr. Match on the base size too, cloning from the base when no
    same-size variant exists. Falls back safely to the base char on any problem.
    """
    if base_char is None:
        return doc.ensure_run_style(bold=bold, italic=italic)
    try:
        header = doc._root._headers[0]
        cps = header._char_properties_element(create=False)
        base_el = cps.find(f"{{{_HH}}}charPr[@id='{base_char}']")
        base_height = base_el.get("height") if base_el is not None else None
        target = (bold, italic)

        def predicate(el) -> bool:
            flags = (el.find(f"{{{_HH}}}bold") is not None, el.find(f"{{{_HH}}}italic") is not None)
            return flags == target and el.get("height") == base_height

        def modifier(el) -> None:
            for tag in ("bold", "italic"):
                for c in el.findall(f"{{{_HH}}}{tag}"):
                    el.remove(c)
            if bold:
                el.append(el.makeelement(f"{{{_HH}}}bold", {}))
            if italic:
                el.append(el.makeelement(f"{{{_HH}}}italic", {}))

        el = header.ensure_char_property(
            predicate=predicate, modifier=modifier, base_char_pr_id=base_char
        )
        return el.get("id") or base_char
    except Exception:
        return base_char  # never inflate the font; worst case the run stays plain-styled


def _find_insertion_marker(doc: HwpxDocument):
    """Locate the paragraph holding an insertion marker ({{body}}/{{appendix}}), if any.

    The first marker in document order wins; whichever is found is consumed (its
    whole paragraph removed) once content is inserted before it.
    """
    for section in doc.sections:
        for paragraph in section.paragraphs:
            text = paragraph.text or ""
            if any(m in text for m in INSERTION_MARKERS):
                return section, paragraph
    return None, None


def _section_opener_run(para_el):
    """Return a paragraph's leading ``<hp:run>`` that carries an ``<hp:secPr>``, if any.

    A 구역's first paragraph opens the section via a (usually textless) run holding
    ``<hp:secPr>`` (+ a column ctrl). When an insertion marker sits *on* such a
    paragraph — e.g. a dedicated empty appendix section whose only paragraph is
    ``{{appendix}}`` — deleting the marker would drop the secPr and collapse the
    section into the previous one. The caller transplants this run onto the first
    authored paragraph instead, so the section boundary survives.
    """
    for run in para_el.findall(f"{{{_HP}}}run"):
        if run.find(f"{{{_HP}}}secPr") is not None:
            return run
    return None


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
    chapter: str | int | None = None,
    table_template: str | None = None,
) -> AuthorResult:
    """Fill a template from Markdown, styled with its own outline styles.

    ``chapter`` sets the chapter label/number used in generated table captions
    (the ``{{chapter_number}}`` placeholder). Pass it explicitly — real documents
    use outline styles too inconsistently (and restart/relabel numbering per
    section, e.g. an appendix in A/B/C) to count chapters reliably. When omitted, a
    best-effort outline-level-0 count is used (fine for clean documents only).

    ``table_template`` is a caption substring/regex naming the table whose format
    generated tables copy — use it when the ``{{table…}}`` token was already
    consumed by a prior ``author`` run (the token is removed on fill). When neither
    a token nor this pattern matches and the Markdown has tables, a warning is added.
    """
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

    # insert at a {{body}}/{{appendix}} marker if present, else append to the last section
    marker_section, marker = _find_insertion_marker(doc)
    target_section = marker_section or doc.sections[-1]
    result.inserted_at_marker = marker is not None

    # tables generated below copy a template table's format (house style)
    caption_pattern = re.compile(re.escape(table_template), re.I) if table_template else None
    ref_element, ref_section, designated = _find_reference_table(doc, caption_pattern)
    table_fmt = _table_format(ref_element) if ref_element is not None else None

    # headings need a linesegarray cloned from a real same-style heading, or Hangul
    # demotes the 2nd+ authored heading to body (see docs/author-backlog.md item C)
    lineseg_by_style = _lineseg_index(doc)

    first_placed = None

    def place(element) -> None:
        """Move a freshly-built element before the marker (else leave appended)."""
        nonlocal first_placed
        if marker is not None:
            element.getparent().remove(element)
            marker.element.addprevious(element)
            if first_placed is None:
                first_placed = element

    def add_runs(para, text: str, base_char: str | None) -> None:
        for seg in inline_segments(text):
            # size-preserving bold/italic (plain ensure_run_style can grab a 20pt
            # charPr); plain text keeps the paragraph style's base char
            char = (
                _emphasis_char(doc, base_char, bold=seg.bold, italic=seg.italic)
                if seg.bold or seg.italic
                else base_char
            )
            para.add_run(seg.text, char_pr_id_ref=char)

    body_style = roles.get("BODY")
    body_para = infos[body_style].para_pr_id if body_style in infos else None

    # chapter label for table captions ({{chapter_number}}). Prefer the explicit
    # value (the AI knows the chapter); else best-effort count of outline level-0
    # paragraphs before the insertion point, incremented per authored "# " heading.
    explicit_chapter = None if chapter is None else str(chapter)
    chapter_count = 0
    if explicit_chapter is None:
        for p in doc.paragraphs:
            if marker is not None and p.element is marker.element:
                break
            pp = doc.paragraph_property(p.para_pr_id_ref)
            if pp and pp.heading and pp.heading.type == "OUTLINE" and pp.heading.level == 0:
                chapter_count += 1

    def chapter_label() -> str | None:
        if explicit_chapter is not None:
            return explicit_chapter
        return str(chapter_count) if chapter_count else None

    blocks = parse_markdown(markdown)
    _resolve_cross_refs(blocks, chapter_label(), result.warnings)
    if any(isinstance(b, TableBlock) for b in blocks) and not designated:
        result.warnings.append(
            "no {{table…}} token found — generated tables use "
            + ("the first table's format" if ref_element is not None else "a generic default")
            + "; mark the reference table's caption with {{table_template}} or pass "
            "--table-template '<caption text>' to copy the house style."
        )

    # contextual heading spacing: a heading gets one blank paragraph above it, sized
    # to its own level (## gap taller than ### gap), UNLESS it hugs its parent — i.e.
    # it sits directly under a shallower heading and the author left no blank line.
    # A fixed style margin can't express this (same style is used both hugging and
    # after content), so the gap is a real paragraph inserted per-heading. Honors an
    # author's explicit blank line (forces a gap) and fills tightly-packed Markdown.
    prev_block: Block | TableBlock | None = None

    def _wants_gap(prev: Block | TableBlock | None, cur: Block) -> bool:
        if prev is None:  # the first authored block never gets a leading gap
            return False
        hugging = (
            isinstance(prev, Block)
            and prev.kind == "heading"
            and prev.level < cur.level
            and cur.blank_before == 0
        )
        return not hugging

    def _emit_heading_gap(cur: Block) -> None:
        h_style = resolve(cur)[0] or body_style  # the heading's own style id
        h_char = doc.style(h_style).char_pr_id_ref if h_style is not None else None
        gap = target_section.add_paragraph(
            "", style_id_ref=body_style, para_pr_id_ref=body_para, include_run=False
        )
        # an (empty) run carrying the heading's char so the blank line's height tracks
        # the heading size; the BODY paraPr keeps it out of the outline (no ghost number)
        gap.add_run("", char_pr_id_ref=h_char)
        place(gap.element)

    for block in blocks:
        gap_needed = (
            isinstance(block, Block)
            and block.kind == "heading"
            and _wants_gap(prev_block, block)
        )
        prev_block = block  # remembered for the next block's hug check
        if gap_needed:
            _emit_heading_gap(block)

        if isinstance(block, TableBlock):
            table = _build_table(
                doc, target_section, block, table_fmt, body_style, body_para,
                ref_element, chapter_label(),
            )
            place(table.paragraph.element)
            result.placed += 1
            continue

        if block.kind == "rule":
            # a Markdown thematic break (`---`) → a full-width horizontal line, as its
            # own (BODY-styled) paragraph; spans the section's text column.
            width = _text_width(target_section) or 14400
            rule_para = target_section.add_paragraph(
                "", style_id_ref=body_style, para_pr_id_ref=body_para, include_run=False
            )
            rule_para.add_line(0, 0, width, 0, treat_as_char=True)
            line_el = rule_para.element.find(f".//{{{_HP}}}line")
            if line_el is not None:
                # python-hwpx writes the line's points in the paragraph namespace
                # (<hp:startPt>/<hp:endPt>), but Hangul requires them in the core
                # namespace (<hc:startPt>/<hc:endPt>) and refuses the file otherwise.
                for tag in ("startPt", "endPt"):
                    pt = line_el.find(f"{{{_HP}}}{tag}")
                    if pt is not None:
                        fixed = line_el.makeelement(f"{{{_HC}}}{tag}", dict(pt.attrib))
                        pt.getparent().replace(pt, fixed)
                # a flat line has zero-height boxes; give them height 1 (consistent
                # with Hangul's own horizontal lines, whose curSz height is 1) so the
                # line always renders
                for tag in ("orgSz", "curSz", "sz"):
                    box = line_el.find(f"{{{_HP}}}{tag}")
                    if box is not None and (box.get("height") or "0") == "0":
                        box.set("height", "1")
            place(rule_para.element)
            result.placed += 1
            continue

        style_id, role = resolve(block)
        if role == "HEADING_1" and explicit_chapter is None:
            chapter_count += 1
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
        info = infos.get(style_id)
        if block.kind == "heading":
            text = _heading_render_text(block, info)
        elif block.kind == "bullet":
            text = _bullet_render_text(block, role, info)
        else:
            text = block.text
        add_runs(para, text, base_char)
        # headings: clone a linesegarray from a real same-style heading so Hangul
        # keeps them as outline headings (item C) — only when one isn't already there
        if (
            block.kind == "heading"
            and str(style_id) in lineseg_by_style
            and para.element.find(f"{{{_HP}}}linesegarray") is None
        ):
            lsa = copy.deepcopy(lineseg_by_style[str(style_id)])
            # python-hwpx >= 2.11 drops a "stale" cache at save time when any
            # lineseg's textpos exceeds the paragraph's text length — the clone
            # came from a (usually longer) template heading, so collapse it to a
            # single lineseg at textpos 0, which is valid for any heading text
            # while keeping the outline flags Hangul looks for.
            segs = lsa.findall(f"{{{_HP}}}lineseg")
            for extra in segs[1:]:
                lsa.remove(extra)
            if segs:
                segs[0].set("textpos", "0")
            para.element.append(lsa)
        place(para.element)
        result.placed += 1

    # clean the {{table…}} designation token out of the reference table's caption
    if designated and ref_element is not None:
        _strip_table_token(ref_element)
        ref_section.mark_dirty()  # so the edited section is re-serialized on save

    if marker is not None:
        # if the marker paragraph opens its 구역 (carries <hp:secPr>), move that
        # secPr-bearing run onto the first authored paragraph before deleting the
        # marker — otherwise the section loses its boundary and collapses into the
        # previous one (a dedicated empty appendix section is exactly this shape).
        opener = _section_opener_run(marker.element)
        if opener is not None and first_placed is not None:
            first_placed.insert(0, opener)
            marker.element.getparent().remove(marker.element)
        elif opener is not None:
            # nothing authored: keep the opener paragraph, just drop the marker text
            for run in list(marker.element.findall(f"{{{_HP}}}run")):
                if run is not opener:
                    marker.element.remove(run)
        else:
            marker.element.getparent().remove(marker.element)

    doc.save_to_path(str(output or template))
    return result
