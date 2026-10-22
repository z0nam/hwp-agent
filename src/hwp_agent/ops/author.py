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
present (the marker is consumed on fill), else appended to the last section —
and in that case a warning names the section it landed in, because house
templates often reserve one section per part (front matter, body, references)
with its own running heads.
``AI:INSTRUCTION``-styled paragraphs are read by
:func:`read_instructions` and stripped on fill. Inline ``**bold**``/``*italic*`` is
currently flattened to text (run-level styling is a later refinement).
"""

from __future__ import annotations

import copy
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from hwpx.document import HwpxDocument

from .equation import equation_run_xml, latex_to_hwp, math_pieces, unescape_dollars
from .form import extract_placeholders
from .links import (
    _escape_xml,
    link_inner_xml,
    link_pieces,
    link_run_xml,
    mask_escapes,
    rendered_text,
    should_left_align,
    unmask_escapes,
)
from .styles import INSTRUCTION, bullet_glyph_name, read_style_system, role_map
from .tablewidth import CHAR_W, compute_column_widths
from .template import (
    FIGURE_TOKEN_RE,
    INSERTION_MARKERS,
    RESIDUAL_TOKEN_RE,
    TABLE_TOKEN_RE,
)

#: tokens marking where authored content is inserted (each on a paragraph of its
#: own); the marker paragraph is consumed (removed) on fill. A full report has
#: several parts, each in its own section with its own running heads: ``{{summary}}``
#: (요약), ``{{intro}}`` (들어가며), ``{{body}}`` (main body), ``{{references}}``
#: (참고문헌), ``{{appendix}}`` (부록). Ordered as they appear front-to-back.
SUMMARY_MARKER = "{{summary}}"
INTRO_MARKER = "{{intro}}"
BODY_MARKER = "{{body}}"
REFERENCES_MARKER = "{{references}}"
APPENDIX_MARKER = "{{appendix}}"

#: a table whose caption carries a ``{{table…}}`` token (e.g. ``{{table}}``,
#: ``{{table_template}}``) is the format reference for generated tables.
_TABLE_TOKEN_RE = TABLE_TOKEN_RE
_FIGURE_TOKEN_RE = FIGURE_TOKEN_RE
_RESIDUAL_TOKEN_RE = RESIDUAL_TOKEN_RE

#: Minimal 1x1 white PNG bytes to replace Preview/PrvImage.png (69 bytes)
_MINIMAL_WHITE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfe\r\xefF\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
)
#: a table designated with an explicit sample modifier (e.g. ``{{table_template:sample}}``)
#: is a dummy format placeholder and is dropped after fill completes.
_TABLE_SAMPLE_RE = re.compile(
    r"\{\{\s*table[\w:-]*:(?:sample|drop|placeholder)\s*\}\}", re.IGNORECASE
)
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_HC = "http://www.hancom.co.kr/hwpml/2011/core"
_HS = "http://www.hancom.co.kr/hwpml/2011/section"
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
    target_section: int = -1  # index of the section the content went into
    links: int = 0  # hyperlink fields created ([text](url) / bare URLs)
    equations: int = 0  # equation objects created ($…$ / $$…$$)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "placed": self.placed,
            "unmapped_roles": self.unmapped_roles,
            "instructions_removed": self.instructions_removed,
            "inserted_at_marker": self.inserted_at_marker,
            "target_section": self.target_section,
            "links": self.links,
            "equations": self.equations,
            "warnings": self.warnings,
        }


def _mask_math(text: str) -> tuple[str, list[str]]:
    """Replace ``$…$`` / ``$$…$$`` spans with private-use placeholders so ``*`` /
    ``**`` inside math aren't read as emphasis (issue #5)."""
    spans: list[str] = []
    out: list[str] = []
    for t, kind in math_pieces(text):
        if kind is None:
            out.append(t)
        else:
            out.append(f"\ue000{len(spans)}\ue001")
            spans.append(f"$${t}$$" if kind == "display" else f"${t}$")
    return "".join(out), spans


def _unmask_math(text: str, spans: list[str]) -> str:
    if not spans:
        return text
    return re.sub("\ue000(\\d+)\ue001", lambda m: spans[int(m.group(1))], text)


def inline_segments(text: str) -> list[Segment]:
    """Split inline ``**bold**`` / ``*italic*`` runs from plain text.

    Math spans (``$…$``) are opaque here — they stay verbatim inside a segment and
    become equation objects at emit time."""
    text = mask_escapes(text)
    text, spans = _mask_math(text)
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
    segments = segments or [Segment(text)]
    for seg in segments:
        seg.text = _unmask_math(seg.text, spans)
    return segments


def plain_text(text: str) -> str:
    """Inline text with emphasis markers removed."""
    return "".join(s.text for s in inline_segments(text))


#: a Markdown footnote reference ``[^id]`` (id = no whitespace/``]``) and its
#: definition line ``[^id]: text`` (pandoc/GFM). The reference is replaced in place
#: by a Hangul footnote control; the definition line is removed from the body.
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\]")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]?(.*)$")


def extract_footnote_defs(
    markdown: str, *, math: bool = True
) -> tuple[str, dict[str, str]]:
    """Split ``[^id]: text`` definition lines out of *markdown*.

    Returns ``(body_markdown_without_definitions, {id: text})``. A definition's
    continuation lines (indented, non-blank) are folded into one note text, matching
    pandoc. The ``[^id]`` references stay in the body text — they are turned into
    footnote controls at run-emit time, each looked up in the returned map."""
    markdown = mask_escapes(markdown, math=math)
    defs: dict[str, str] = {}
    out: list[str] = []
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        m = _FOOTNOTE_DEF_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        fid, text = m.group(1), m.group(2).strip()
        i += 1
        while i < len(lines) and lines[i].strip() and lines[i][:1] in (" ", "\t"):
            text = f"{text} {lines[i].strip()}".strip()
            i += 1
        defs[fid] = text  # a later redefinition wins (last-one, like pandoc warns)
    return "\n".join(out), defs


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _count_footnotes(doc: HwpxDocument) -> int:
    """How many footnote controls already exist in the document (any section)."""
    return sum(
        len(s.element.findall(f".//{{{_HP}}}footNote")) for s in doc.sections
    )


def _section_has_footnote_pr(section) -> bool:
    return section.element.find(f".//{{{_HP}}}footNotePr") is not None


def _footnote_style(
    doc: HwpxDocument, infos: dict, body_style: str | None, body_para
) -> tuple[str | None, object, object]:
    """Resolve (styleIDRef, paraPrIDRef, charPrIDRef) for footnote-note paragraphs.

    Prefer a template style named like 「각주」/footnote (JRI_각주, 각주1, …); fall back
    to the BODY style when the template has none (issue #23). The char is read off the
    chosen style so the note text matches the template's footnote font."""
    fn_sid = body_style
    for i in infos.values():
        name = (i.name or "").lower()
        if "각주" in (i.name or "") or "footnote" in name:
            fn_sid = i.style_id
            break
    fn_para = body_para
    fn_char = None
    if fn_sid is not None:
        info = infos.get(fn_sid)
        if info is not None and info.para_pr_id is not None:
            fn_para = info.para_pr_id
        try:
            fn_char = doc.style(fn_sid).char_pr_id_ref
        except Exception:
            fn_char = None
    return fn_sid, fn_para, fn_char


#: Hangul's default footnote number format (used when the section has no footNotePr)
_DEFAULT_FN_FORMAT = {
    "type": "DIGIT", "userChar": "", "prefixChar": "", "suffixChar": ")", "supscript": "0",
}


def _section_footnote_format(section) -> dict[str, str]:
    """The section's ``<hp:footNotePr><hp:autoNumFormat>`` attributes (numbering
    type, user char, decorations, superscript), else Hangul's default ``1)``."""
    fmt = section.element.find(f".//{{{_HP}}}footNotePr/{{{_HP}}}autoNumFormat")
    return dict(fmt.attrib) if fmt is not None else dict(_DEFAULT_FN_FORMAT)


def _build_footnote_run(
    number: int, inst_id: int, text: str,
    fn_sid: str | None, fn_para, fn_char, body_char,
    num_format: dict[str, str] | None = None,
    *,
    links: bool = False,
    link_char=None,
):
    """Build a body ``<hp:run>`` carrying a footnote control with *text* as its note.

    The reference mark is a ``<hp:ctrl><hp:footNote>`` whose ``<hp:subList>`` holds one
    note paragraph (footnote style + autoNum); Hangul renumbers continuously on open,
    so the stored *number* is just a seed. No ``<hp:linesegarray>`` is emitted — Hangul
    (and rhwp) lay the note out from the text.

    *num_format* is the section's footnote ``autoNumFormat`` (Roman, circled, user
    char, decorations, superscript …), copied so the note number matches the
    template's own footnote settings instead of a hard-coded ``1)``.

    With *links*, ``[표시](url)`` / bare URLs in the note become hyperlink fields
    (issue #27), each in its own run (in *link_char*, else the note char); text
    without links yields the same single run as before."""
    from lxml import etree

    def attr(v):
        return "" if v is None else str(v)

    fmt = num_format or _DEFAULT_FN_FORMAT
    fmt_attrs = "".join(f' {k}="{_xml_escape(attr(v))}"' for k, v in fmt.items())
    suffix = fmt.get("suffixChar") or ""
    # footNote@suffixChar is the decoration's code point (")" → 41), as Hangul writes it
    fn_suffix = f' suffixChar="{ord(suffix[0])}"' if suffix else ""
    run_char = f' charPrIDRef="{attr(body_char)}"' if body_char is not None else ""
    p_char = f' charPrIDRef="{attr(fn_char)}"' if fn_char is not None else ""
    pieces = link_pieces(text) if links else [(text, None)]
    first = ""
    if pieces and pieces[0][1] is None:
        first, pieces = pieces[0][0], pieces[1:]
    l_char = link_char if link_char is not None else fn_char
    l_attr = f' charPrIDRef="{attr(l_char)}"' if l_char is not None else ""
    rest = "".join(
        f"<hp:run{l_attr}>{link_inner_xml(unmask_escapes(t), u)}</hp:run>" if u is not None
        else f"<hp:run{p_char}><hp:t>{_xml_escape(unmask_escapes(t))}</hp:t></hp:run>"
        for t, u in pieces
    )
    xml = (
        f'<hp:run xmlns:hp="{_HP}"{run_char}>'
        f'<hp:ctrl><hp:footNote number="{number}"{fn_suffix} instId="{inst_id}">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
        f'hasTextRef="0" hasNumRef="0">'
        f'<hp:p id="0" paraPrIDRef="{attr(fn_para)}" styleIDRef="{attr(fn_sid)}" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run{p_char}>'
        f'<hp:ctrl><hp:autoNum num="{number}" numType="FOOTNOTE">'
        f"<hp:autoNumFormat{fmt_attrs}/></hp:autoNum></hp:ctrl>"
        f"<hp:t>{_xml_escape(unmask_escapes(first))}</hp:t></hp:run>{rest}"
        f"</hp:p></hp:subList></hp:footNote></hp:ctrl></hp:run>"
    )
    return etree.fromstring(xml)


def _split_table_row(line: str) -> list[str]:
    """Split a pipe-table row into trimmed cells (outer pipes optional).

    A ``|`` inside ``$…$`` math (``\\left| x \\right|``) is part of the formula,
    not a column break."""
    line = mask_escapes(line)
    s, spans = _mask_math(line.strip())
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [_unmask_math(c.strip(), spans) for c in s.split("|")]


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


def parse_markdown(markdown: str, *, math: bool = True) -> list[Block | TableBlock]:
    """Parse Markdown into headings, paragraphs, bullets, ordered lists, and tables.

    With *math*, a ``$$ … $$`` block standing alone (one line or spread over
    several) becomes a ``"math"`` block holding the LaTeX source (issue #5).

    Inline ``**bold**`` / ``*italic*`` markers are kept in ``Block.text`` and
    resolved into runs at fill time via :func:`inline_segments`. Pipe tables
    (header row + ``|---|`` delimiter + body rows) become :class:`TableBlock`.
    """
    markdown = mask_escapes(markdown, math=math)
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
        if math and line.strip().startswith("$$"):
            latex = _display_math(lines, i)
            if latex is not None:
                flush()
                src, i = latex
                emit(Block("math", 0, src))
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


def _display_math(lines: list[str], i: int) -> tuple[str, int] | None:
    """A ``$$`` display block starting at line *i*: ``(latex, next_line_index)``, or
    None when the line is not a standalone display block (e.g. ``$$a$$ and text``)."""
    first = lines[i].strip()[2:]
    if "$$" in first:  # one line: $$ … $$ with nothing after
        body, _, tail = first.partition("$$")
        if tail.strip() or not body.strip():
            return None
        return body.strip(), i + 1
    buf = [first]
    j = i + 1
    while j < len(lines):
        ln = lines[j].rstrip()
        if "$$" in ln:
            body, _, tail = ln.partition("$$")
            if tail.strip():
                return None
            buf.append(body)
            return "\n".join(buf).strip(), j + 1
        if not ln.strip():  # a blank line ends the paragraph — unclosed $$
            return None
        buf.append(ln)
        j += 1
    return None


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


def _char_height_lookup(doc: HwpxDocument):
    """Return ``charPrIDRef -> height`` (1/100 pt) from the document header."""
    try:
        cps = doc._root._headers[0]._char_properties_element(create=False)
    except Exception:
        cps = None

    def height(char_id) -> int | None:
        if cps is None or char_id is None:
            return None
        el = cps.find(f"{{{_HH}}}charPr[@id='{char_id}']")
        v = el.get("height") if el is not None else None
        return int(v) if v and v.isdigit() else None

    return height


def _table_format(tbl, char_height=None) -> TableFormat:
    """Read header / body / last-row zones from a reference table.

    *char_height* (``charPrIDRef -> height`` in 1/100 pt, optional) lets the body-row
    seed be checked against one line of the body font — see :func:`_body_row_seed`.
    """
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
        # Height for every generated body row = a *single-line* seed, NOT this
        # template's saved body-row height. A template whose sample table has a
        # multi-line data row stores a tall cellSz on it (issue #21: 9935 ≈ 35mm);
        # copying that onto every generated row bloats short rows. Hangul treats
        # cellSz height as a minimum and grows a cell to fit on open, so a tight seed
        # is safe (a tall one is not — it can't shrink). See _body_row_seed.
        line = _line_height_estimate(fmt.body, char_height)
        tight = _body_row_seed(fmt.header, body_rows, line)
        if tight is not None:
            fmt.first_body.height = fmt.last.height = fmt.body.height = tight
    else:
        fmt.first_body = fmt.body = fmt.last = fmt.header
    if note_rows:
        fmt.note = note_rows[-1]
    return fmt


def _line_height_estimate(zone: Zone, char_height) -> int | None:
    """One text line of *zone*'s content style, as a cellSz height (HWPUNIT).

    Font height (charPr ``height``, 1/100 pt ≈ HWPUNIT) × 1.6 (Hangul's default 160%
    line spacing) + the cell's top/bottom margins. Coarse, but enough to tell a
    one-line row from a saved multi-line one. ``None`` when the font size is unknown.
    """
    if char_height is None or zone.char is None:
        return None
    h = char_height(zone.char)
    if not h:
        return None
    margin = zone.margin or {}
    pad = sum(int(margin.get(k, "0") or 0) for k in ("top", "bottom"))
    return int(h * 1.6) + pad


def _body_row_seed(header: Zone, body_rows: list[Zone], line: int | None) -> str | None:
    """Single-line height seed for generated body rows.

    * The template's tightest body row is used as-is when it's about one line of the
      body font (≤ 1.5 lines) — the designer's own row height (bundled: 2183).
    * When every body row is clearly multi-line (issue #21: 9935), fall back to the
      header row **only if it can hold a body line**, else to the line estimate — a
      header with a smaller font/shorter row would clip body text in rhwp (which,
      unlike Hangul, doesn't grow cells), so it must not seed the body by itself.
    * Without a font size, the previous rule (tightest of header + body) applies.
    """
    body_min = _tight_row_height(body_rows)
    if line is None:
        return _tight_row_height([header, *body_rows])
    if body_min is not None and int(body_min) <= line * 1.5:
        return body_min
    head = _tight_row_height([header])
    if head is not None and line <= int(head) and (body_min is None or int(head) < int(body_min)):
        return head
    return str(max(line, CHAR_W))


def _tight_row_height(zones: list[Zone]) -> str | None:
    """Smallest text-capable ``cellSz`` height among *zones* — a single-line proxy.

    Rows shorter than one CJK glyph (``CHAR_W``) can't hold a line of text — they are
    layout spacers (form-style reference tables carry 382/665 rows between content
    rows). Seeding from one would clip even a single line in rhwp, so they're skipped.
    """
    heights = []
    for z in zones:
        if z.height and z.height.isdigit() and int(z.height) >= CHAR_W:
            heights.append(int(z.height))
    return str(min(heights)) if heights else None


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


def _is_neutralized_reference_table(tbl_element) -> bool:
    """Return True if *tbl_element* is a neutralized format reference table.

    Such a table was created solely to preserve house styles in stripped templates:
      - Header row cells are either blank or standard placeholders ('구분', '항목', '내용').
      - All non-header (data and note) cells are completely empty.
    """
    trs = tbl_element.findall(f"{{{_HP}}}tr")
    if not trs:
        return False
    generic_headers = {"", "구분", "항목", "내용"}
    for ri, tr in enumerate(trs):
        for tc in tr.findall(f"{{{_HP}}}tc"):
            text = "".join(t.text or "" for t in tc.iter(f"{{{_HP}}}t")).strip()
            is_header = tc.get("header") == "1" or ri == 0
            if is_header:
                if text not in generic_headers:
                    return False
            else:
                if text:
                    return False
    return True


def _is_sample_reference_table(tbl_element) -> bool:
    """Return True if *tbl_element* is a dummy format-reference table marked for dropping.

    To protect user-defined reference tables (e.g. empty form tables with {{table_template}}),
    a table is dropped on fill completion only when BOTH:
      1. Its caption carries an explicit sample modifier (e.g. ``{{table_template:sample}}``).
      2. Its cells contain only generic placeholders ('구분', '항목', '내용') and blank body cells.
    """
    if not _TABLE_SAMPLE_RE.search(_caption_text(tbl_element)):
        return False
    return _is_neutralized_reference_table(tbl_element)


def _find_top_sec_paragraph(el):
    """Find the top-level section paragraph enclosing *el*, or *el* itself if it is one."""
    cur = el
    while cur is not None:
        parent = cur.getparent()
        if parent is not None and parent.tag == f"{{{_HS}}}sec":
            return cur if cur.tag == f"{{{_HP}}}p" else None
        cur = parent
    return None


def _remove_top_paragraph_safely(p, section) -> None:
    """Remove *p* from *section*, preserving any <hp:secPr> if present."""
    if p.find(f".//{{{_HP}}}secPr") is not None:
        # Paragraph carries section property: keep the secPr run, strip other children
        for child in list(p):
            if child.tag != f"{{{_HP}}}run" or child.find(f"{{{_HP}}}secPr") is None:
                p.remove(child)
    else:
        parent = p.getparent()
        if parent is not None:
            parent.remove(p)
    section.mark_dirty()


def _drop_reference_tables(doc: HwpxDocument) -> int:
    """Drop format-reference and dummy template tables from the document.

    Any table designated with {{table_template}} (with or without a modifier like :sample)
    was used solely as a styling reference and must not survive in the final output.
    """
    dropped = 0
    top_ps_to_drop = []
    for section in doc.sections:
        for tbl in list(section.element.iter(f"{{{_HP}}}tbl")):
            cap_txt = _caption_text(tbl)
            if _TABLE_TOKEN_RE.search(cap_txt) or _is_sample_reference_table(tbl):
                tp = _find_top_sec_paragraph(tbl)
                if tp is not None and tp not in [item[1] for item in top_ps_to_drop]:
                    top_ps_to_drop.append((section, tp))

    for section, p in top_ps_to_drop:
        _remove_top_paragraph_safely(p, section)
        dropped += 1
    return dropped


def _drop_sample_tables(doc: HwpxDocument) -> int:
    """Drop any dummy format reference tables from the document (kept for backwards compatibility).
    """
    return _drop_reference_tables(doc)


def _drop_figure_template_paragraphs(doc: HwpxDocument) -> int:
    """Remove {{figure_template}} reference paragraphs and unreferenced BinData."""
    dropped = 0
    top_ps_to_drop = []
    for section in doc.sections:
        for p in list(section.element.iter(f"{{{_HP}}}p")):
            text = "".join(p.itertext())
            if _FIGURE_TOKEN_RE.search(text):
                tp = _find_top_sec_paragraph(p)
                if tp is not None and tp not in [item[1] for item in top_ps_to_drop]:
                    top_ps_to_drop.append((section, tp))

    if not top_ps_to_drop:
        return 0

    dropped_refs: set[str] = set()
    for section, p in top_ps_to_drop:
        for img in p.iter(f"{{{_HC}}}img"):
            bid = img.get("binaryItemIDRef")
            if bid:
                dropped_refs.add(bid)
        _remove_top_paragraph_safely(p, section)
        dropped += 1

    remaining_refs: set[str] = set()
    for section in doc.sections:
        for el in section.element.iter():
            bid = el.get("binaryItemIDRef")
            if bid:
                remaining_refs.add(bid)

    try:
        for h in getattr(doc._root, "_headers", []):
            el = getattr(h, "_element", None)
            if el is not None:
                for sub in el.iter():
                    bid = sub.get("binaryItemIDRef")
                    if bid:
                        remaining_refs.add(bid)
    except Exception:
        pass

    try:
        for mp in getattr(doc._root, "_master_pages", []):
            el = getattr(mp, "_element", None)
            if el is not None:
                for sub in el.iter():
                    bid = sub.get("binaryItemIDRef")
                    if bid:
                        remaining_refs.add(bid)
    except Exception:
        pass

    unreferenced = dropped_refs - remaining_refs
    if unreferenced:
        manifest_el = doc._package._manifest_element()
        if manifest_el is not None:
            for item in list(manifest_el):
                item_id = item.get("id")
                if item_id in unreferenced:
                    href = item.get("href")
                    doc._package.remove_manifest_item(item_id)
                    if href and doc._package.has_part(href):
                        doc._package.delete(href)

    return dropped


def _update_preview_parts(doc: HwpxDocument) -> None:
    """Replace Preview/PrvText.txt with output body text and PrvImage.png with a minimal PNG."""
    lines = []
    total_len = 0
    max_chars = 2000
    for sec in doc.sections:
        for p in sec.paragraphs:
            txt = (p.text or "").strip()
            if txt:
                lines.append(txt)
                total_len += len(txt)
                if total_len >= max_chars:
                    break
        if total_len >= max_chars:
            break

    prv_text = "\r\n".join(lines)
    doc._package.set_part("Preview/PrvText.txt", prv_text.encode("utf-8"))
    doc._package.set_part("Preview/PrvImage.png", _MINIMAL_WHITE_PNG)


def _find_residual_tokens(doc: HwpxDocument) -> list[str]:
    """Find leftover {{...}} markers across document paragraphs and captions."""
    tokens = []
    for sec in doc.sections:
        for p in sec.element.iter(f"{{{_HP}}}p"):
            text = "".join(p.itertext())
            for match in _RESIDUAL_TOKEN_RE.findall(text):
                tokens.append(f"{{{{{match.strip()}}}}}")
    return tokens


class _CaptionParaAdapter:
    """Minimal paragraph adapter so :func:`add_runs` can append to a caption paragraph."""

    def __init__(self, p_el) -> None:
        self.element = p_el

    def add_run(self, text: str = "", *, char_pr_id_ref: str | None = None, **kwargs):
        from lxml import etree

        c_attr = f' charPrIDRef="{char_pr_id_ref}"' if char_pr_id_ref is not None else ""
        esc = _escape_xml(unmask_escapes(text))
        run = etree.fromstring(f'<hp:run xmlns:hp="{_HP}"{c_attr}><hp:t>{esc}</hp:t></hp:run>')
        self.element.append(run)
        return run


def _fill_caption_runs(
    cap_el,
    title: str,
    *,
    run_writer=None,
) -> None:
    """Populate *cap_el* with *title*, resolving Markdown links and inline styles.

    Keeps the caption framing (e.g. ``>`` or ``]``) on the last text node of the
    auto-number run, and appends runs for *title* via *run_writer* (an adapter to
    ``add_runs``). Shared between table captions and future figure captions (#31).
    When *run_writer* is None, falls back to setting plain text on the last text node.
    """
    texts = cap_el.findall(f".//{{{_HP}}}t")
    if not texts:
        return

    last = texts[-1].text or ""
    m = re.match(r"^([^\]>]*[>\]])", last)
    framing = m.group(1) if m else _TABLE_TOKEN_RE.sub("", last).rstrip()

    if not title:
        texts[-1].text = framing.strip()
        return

    if run_writer is None:
        texts[-1].text = unmask_escapes(f"{framing} {title}".strip())
        return

    texts[-1].text = f"{framing} " if framing else ""
    base_run = texts[-1].getparent()
    base_char = base_run.get("charPrIDRef") if base_run is not None else None
    p_el = base_run.getparent() if base_run is not None else cap_el.find(f".//{{{_HP}}}p")
    if p_el is None:
        texts[-1].text = unmask_escapes(f"{framing} {title}".strip())
        return

    lsa = p_el.find(f"{{{_HP}}}linesegarray")
    if lsa is not None:
        p_el.remove(lsa)
    adapter = _CaptionParaAdapter(p_el)
    run_writer(adapter, title, base_char)
    if lsa is not None:
        p_el.append(lsa)


def _clone_caption(ref_el, title: str, chapter: str | None, *, run_writer=None):
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

    _fill_caption_runs(clone, title, run_writer=run_writer)
    return clone


def _style_cell(
    cell, zone: Zone, col: int, ncols: int, text: str, text_writer=None,
    *, para_resolver=None,
) -> None:
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
    chosen_para = zone.para if zone.para is not None else cp.para_pr_id_ref
    if para_resolver is not None:
        chosen_para = para_resolver(chosen_para, text)
    if chosen_para is not None:
        cp.para_pr_id_ref = chosen_para
    cp.clear_text()
    # cell text is plain (no inline-bold variant): ensure_run_style ignores the cell
    # base size and would blow up the font, so cells keep their content char style.
    if text_writer is not None:  # resolves [^id] footnote references in the cell
        text_writer(cp, plain_text(text), zone.char, zone.char)
    else:
        cp.add_run(unmask_escapes(plain_text(text)), char_pr_id_ref=zone.char)


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


def _fit_table_width_by_content(table_el, block: TableBlock, text_width: int) -> None:
    """Size columns by how much content each holds (default), summing to *text_width*.

    Equal columns waste width when content piles into one column; this gives each
    column a share by √(content) with a min-clamp so the longest word isn't cut
    (shared with ``form autofit`` via :func:`compute_column_widths`). Every row is
    still ``n_cols`` cells at this point (the note row is merged later), so one
    per-column width applies to all rows.
    """
    ncols = block.n_cols
    contents: list[float] = [0.0] * ncols
    longest: list[int] = [0] * ncols
    for row in block.rows:
        for c in range(ncols):
            cell = row[c] if c < len(row) else ""
            contents[c] += len(cell)
            longest[c] = max(longest[c], max((len(w) for w in cell.split()), default=0))
    widths = compute_column_widths(contents, longest, text_width)
    sz = table_el.find(f"{{{_HP}}}sz")
    if sz is not None:
        sz.set("width", str(text_width))
    for tr in table_el.findall(f"{{{_HP}}}tr"):
        cell_szs = [tc.find(f"{{{_HP}}}cellSz") for tc in tr.findall(f"{{{_HP}}}tc")]
        cell_szs = [c for c in cell_szs if c is not None]
        if len(cell_szs) == ncols:
            for c, csz in enumerate(cell_szs):
                csz.set("width", str(widths[c]))
        else:  # already-merged/odd row: fill the width so the row still sums right
            for csz in cell_szs:
                csz.set("width", str(text_width))


def _grow_rows_for_content(table_el, row_texts: list[list[str]]) -> None:
    """Grow each row's cell height to the lines its content needs.

    The tight single-line seed (:func:`_tight_row_height`) fixes the single-line bloat
    of issue #21, but rhwp — unlike Hangul — does **not** auto-grow a cell to fit
    overflowing text, so a genuinely multi-line cell left at the one-line seed is
    clipped in the rhwp preview (and in a template whose rows were tall, this would be
    a visible regression). Estimate the wrapped-line count per cell from its fitted
    column width (``CHAR_W`` ≈ one CJK glyph) and set the row height to
    ``seed × max_lines``. Hangul still reconciles the exact height on open; this just
    keeps the deterministic preview honest and bounds any over-estimate to whole lines.

    *row_texts* is aligned with the table's ``<hp:tr>`` rows (data rows, then the
    merged note row as a one-cell list). Length is measured on the **rendered** text
    (:func:`plain_text` — ``**``/``*`` delimiters aren't drawn, so counting them would
    push a fitting row onto a phantom extra line).
    """
    rows = table_el.findall(f"{{{_HP}}}tr")
    for tr, texts in zip(rows, row_texts, strict=False):
        cells = tr.findall(f"{{{_HP}}}tc")
        max_lines = 1
        for c, tc in enumerate(cells):
            sz = tc.find(f"{{{_HP}}}cellSz")
            if sz is None:
                continue
            width = int(sz.get("width", "0"))
            mg = tc.find(f"{{{_HP}}}cellMargin")
            inset = 0
            if mg is not None:
                inset = int(mg.get("left", "0")) + int(mg.get("right", "0"))
            usable = max(width - inset, CHAR_W)
            cpl = max(1, usable // CHAR_W)  # chars per line (coarse, CJK-width)
            # rendered text: emphasis delimiters aren't drawn and a [^id] reference
            # becomes a one-glyph superscript mark, so neither counts toward wrapping
            text = (
                _FOOTNOTE_REF_RE.sub("¹", rendered_text(plain_text(texts[c])))
                if c < len(texts) else ""
            )
            lines = sum(max(1, -(-len(seg) // cpl)) for seg in text.split("\n")) or 1
            max_lines = max(max_lines, lines)
        if max_lines > 1:
            for tc in cells:
                sz = tc.find(f"{{{_HP}}}cellSz")
                if sz is not None and sz.get("height", "0").isdigit():
                    sz.set("height", str(int(sz.get("height")) * max_lines))


def _caption_extent(table_el) -> int:
    """Vertical space a TOP/BOTTOM caption adds to the table object's box.

    Hangul counts a top/bottom ``<hp:caption>`` inside the ``<hp:tbl>`` height: the
    caption ``gap`` plus its text lines (the bundled template: rows 10682 + gap 850 +
    one 950 line ≈ the stored 12543). LEFT/RIGHT captions sit beside the table and add
    width, not height.

    The line **count** is estimated from the caption's *current* text and width — a
    caption cloned from the reference keeps the reference's layout cache, which still
    describes the old (usually one-line) title, so a longer authored title that wraps
    would be undercounted. Only the per-line **height** is taken from that cache (it
    reflects the caption style's font/spacing), else a CJK line at 160%.
    """
    cap = table_el.find(f"{{{_HP}}}caption")
    if cap is None or (cap.get("side") or "TOP").upper() not in ("TOP", "BOTTOM"):
        return 0
    gap = int(cap.get("gap", "0") or 0)
    tbl_sz = table_el.find(f"{{{_HP}}}sz")
    width = int(cap.get("lastWidth") or (tbl_sz.get("width") if tbl_sz is not None else 0)
                or 0)
    cpl = max(1, width // CHAR_W) if width else None
    total = 0
    for p in cap.iter(f"{{{_HP}}}p"):
        seg = p.find(f".//{{{_HP}}}lineseg")
        line_h = (
            int(seg.get("vertsize", "0") or 0) + int(seg.get("spacing", "0") or 0)
            if seg is not None else 0
        ) or int(CHAR_W * 1.6)
        # +2 for the auto-number digits, which live in a control, not in <hp:t>
        n_chars = len("".join(t.text or "" for t in p.iter(f"{{{_HP}}}t"))) + 2
        lines = max(1, -(-n_chars // cpl)) if cpl else 1
        total += line_h * lines
    return gap + total


def _fit_table_height(table_el) -> None:
    """Set the table's own ``<hp:sz height>`` to its rows plus any top/bottom caption.

    ``add_table`` leaves the table box at a loose default (rowCnt × ~3600), unrelated
    to the tight per-cell heights we style in (issue #21: a 10-row table stayed at
    36000 while its rows summed to far less). Sum the tallest cell in each row, and add
    the caption's extent (:func:`_caption_extent`) — call this **after** the caption is
    attached, or rhwp gets an undersized box and overlaps the caption/table with the
    following content. Hangul recomputes it on open; the seed keeps rhwp honest.
    """
    sz = table_el.find(f"{{{_HP}}}sz")
    if sz is None:
        return
    total = 0
    for tr in table_el.findall(f"{{{_HP}}}tr"):
        row_h = [
            int(c.get("height", "0"))
            for tc in tr.findall(f"{{{_HP}}}tc")
            if (c := tc.find(f"{{{_HP}}}cellSz")) is not None
        ]
        total += max(row_h, default=0)
    if total > 0:
        sz.set("height", str(total + _caption_extent(table_el)))


def _build_table(
    doc, section, block: TableBlock, fmt, body_style, body_para, ref_el, chapter=None,
    *, equal_columns: bool = False, text_writer=None, para_resolver=None,
    run_writer=None,
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
        if equal_columns:
            _fit_table_width(table.element, tw)
        else:
            _fit_table_width_by_content(table.element, block, tw)

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
            _style_cell(
                cell, zone, c, ncols, row[c] if c < len(row) else "", text_writer,
                para_resolver=para_resolver,
            )

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
        _style_cell(
            table.cell(note_r, 0), fmt.note, 0, 1, note_text, text_writer,
            para_resolver=para_resolver,
        )
        # a populated note row needs real height: the reference's (empty) note row
        # often stores height 0, which rhwp would draw as a clipped zero-height row
        note_sz = table.cell(note_r, 0).element.find(f"{{{_HP}}}cellSz")
        seed = fmt.body.height if fmt is not None else None
        if (
            note_text and note_sz is not None and seed
            and int(note_sz.get("height", "0") or 0) <= 0
        ):
            note_sz.set("height", seed)

    # caption "<표 N-M> {title}" cloned from the reference (auto-number + 표제목 style)
    if block.caption and ref_el is not None:
        clone = _clone_caption(ref_el, block.caption, chapter, run_writer=run_writer)
        if clone is not None:
            first_tr = table.element.find(f"{{{_HP}}}tr")
            if first_tr is not None:
                first_tr.addprevious(clone)
            else:
                table.element.append(clone)

    # grow multi-line rows off the tight seed (rhwp doesn't auto-grow), then set the
    # table box to rows + caption — after the caption is attached, so it's counted
    row_texts = [list(r) for r in data_rows]
    if has_note:
        row_texts.append([block.note.replace("\n", "  ") if block.note else ""])
    _grow_rows_for_content(table.element, row_texts)
    _fit_table_height(table.element)
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


#: link look forced by ``--blue-links`` (Hangul's own hyperlink colour)
_LINK_COLOR = "#0000FF"


def _blue_link_char(doc: HwpxDocument, base_char):
    """A blue, underlined variant of *base_char* for hyperlink text (``--blue-links``).

    Same font/size/emphasis as the surrounding text — only ``textColor`` and the
    ``<hh:underline>`` differ — so an existing identical variant is reused. Falls back
    to the base char on any problem (the link still works, it just isn't coloured)."""
    from lxml import etree

    def norm(el) -> bytes:
        c = copy.deepcopy(el)
        c.attrib.pop("id", None)
        c.set("textColor", _LINK_COLOR)
        for u in c.findall(f"{{{_HH}}}underline"):
            c.remove(u)
        return etree.tostring(c)

    try:
        header = doc._root._headers[0]
        cps = header._char_properties_element(create=False)
        base_el = cps.find(f"{{{_HH}}}charPr[@id='{base_char}']") if base_char is not None else None
        if base_el is None:
            base_el = cps.find(f"{{{_HH}}}charPr")
        base_norm = norm(base_el)

        def predicate(el) -> bool:
            u = el.find(f"{{{_HH}}}underline")
            return (
                el.get("textColor") == _LINK_COLOR
                and u is not None and u.get("type") == "BOTTOM"
                and norm(el) == base_norm
            )

        def modifier(el) -> None:
            el.set("textColor", _LINK_COLOR)
            for u in el.findall(f"{{{_HH}}}underline"):
                el.remove(u)
            u = el.makeelement(
                f"{{{_HH}}}underline",
                {"type": "BOTTOM", "shape": "SOLID", "color": _LINK_COLOR},
            )
            # schema order: … italic, bold, underline, strikeout, …
            strike = el.find(f"{{{_HH}}}strikeout")
            if strike is not None:
                strike.addprevious(u)
            else:
                el.append(u)

        el = header.ensure_char_property(
            predicate=predicate, modifier=modifier,
            base_char_pr_id=base_el.get("id"),
        )
        return el.get("id") or base_char
    except Exception:
        return base_char


def _left_para_pr(
    doc: HwpxDocument,
    base_para_pr_id: str | int | None,
    cache: dict[str, str],
) -> str | None:
    """Return a LEFT-aligned variant of *base_para_pr_id*, reusing variants when possible.

    Issue #35:
    - If base_para_pr_id is None, returns None.
    - If already cached, returns the cached id.
    - If the base paraPr already has horizontal="LEFT" or is not JUSTIFY (e.g. CENTER, RIGHT),
      returns the base id without duplicating.
    - Otherwise clones the base paraPr, sets <hh:align horizontal="LEFT">,
      appends it to the end of <hh:paraProperties> with max(existing_id)+1,
      updates itemCnt, marks the header dirty, and caches the new id.
    """
    if base_para_pr_id is None:
        return None
    key = str(base_para_pr_id)
    if key in cache:
        return cache[key]

    try:
        header = doc._root._headers[0]
        pps = header._para_properties_element(create=False)
        if pps is None:
            cache[key] = key
            return key
        base_el = pps.find(f"{{{_HH}}}paraPr[@id='{key}']")
        if base_el is None:
            cache[key] = key
            return key

        align_el = base_el.find(f"{{{_HH}}}align")
        # Rule (4): only JUSTIFY is modified. Existing LEFT or CENTER/RIGHT are untouched.
        if align_el is not None and align_el.get("horizontal") != "JUSTIFY":
            cache[key] = key
            return key

        existing_ids = [
            int(p.get("id"))
            for p in pps.findall(f"{{{_HH}}}paraPr")
            if p.get("id") and p.get("id").isdigit()
        ]
        next_id = str(max(existing_ids, default=-1) + 1)

        new_el = copy.deepcopy(base_el)
        new_el.set("id", next_id)
        new_align = new_el.find(f"{{{_HH}}}align")
        if new_align is not None:
            new_align.set("horizontal", "LEFT")
        else:
            new_align = new_el.makeelement(
                f"{{{_HH}}}align",
                {"horizontal": "LEFT", "vertical": "BASELINE"},
            )
            new_el.insert(0, new_align)

        pps.append(new_el)
        pps.set("itemCnt", str(len(pps.findall(f"{{{_HH}}}paraPr"))))
        header.mark_dirty()

        cache[key] = next_id
        return next_id
    except Exception:
        cache[key] = key
        return key


def _find_insertion_marker(doc: HwpxDocument, marker_token: str | None = None):
    """Locate the paragraph holding an insertion marker, if any.

    With *marker_token* given, only that exact token is matched (for filling one
    named part). With no token — a single-part ``write`` — the main body is the
    intended target, so ``{{body}}`` is preferred **wherever** it sits; only if
    the template has no ``{{body}}`` do we fall back to the first of
    :data:`INSERTION_MARKERS` in document order. This keeps main-body Markdown out
    of ``{{intro}}`` when a multi-marker template happens to place intro first
    (PR #16 / Codex P1). Whichever is found is consumed (its paragraph removed)
    once content is inserted before it.
    """
    if marker_token:
        markers: tuple[str, ...] = (marker_token,)
    elif any(
        BODY_MARKER in (p.text or "")
        for section in doc.sections
        for p in section.paragraphs
    ):
        markers = (BODY_MARKER,)
    else:
        markers = INSERTION_MARKERS
    for section in doc.sections:
        for paragraph in section.paragraphs:
            text = paragraph.text or ""
            if any(m in text for m in markers):
                return section, paragraph
    return None, None


def _strip_unfilled_markers(doc: HwpxDocument) -> int:
    """Remove any :data:`INSERTION_MARKERS` paragraphs left unfilled.

    A build that supplies only some parts (e.g. ``body`` only) leaves the other
    markers (``{{intro}}``, ``{{references}}``) sitting in the template; a literal
    ``{{references}}`` must never survive into the output (PR #16 / Codex P2). A
    marker that opens its 구역 keeps its (now blank) paragraph so the section
    boundary/secPr survives; otherwise the whole paragraph is dropped. Returns the
    number of markers stripped.
    """
    stripped = 0
    for section in doc.sections:
        changed = False
        for paragraph in list(section.paragraphs):
            if not any(m in (paragraph.text or "") for m in INSERTION_MARKERS):
                continue
            el = paragraph.element
            opener = _section_opener_run(el)
            if opener is not None:
                # keep the section opener paragraph; drop only the marker text runs
                for run in list(el.findall(f"{{{_HP}}}run")):
                    if run is not opener:
                        el.remove(run)
            else:
                el.getparent().remove(el)
            stripped += 1
            changed = True
        if changed:
            section.mark_dirty()
    return stripped


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
    marker_token: str | None = None,
    equal_columns: bool = False,
    strip_other_markers: bool = False,
    strip_table_token: bool = True,
    links: bool = True,
    blue_links: bool = False,
    math: bool = True,
    link_align: str = "auto",
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

    ``links`` (default on) turns ``[표시](url)`` and bare ``http(s)://`` URLs — in
    body text, table cells and footnote notes — into Hangul hyperlink fields
    (issue #27); off keeps them as plain text. Link text takes the template's
    「하이퍼링크」 style char when it has one; ``blue_links`` forces a blue-underlined
    variant of the surrounding char instead.

    ``math`` (default on) turns ``$…$`` (inline) and standalone ``$$…$$`` (display)
    LaTeX into Hangul equation objects (issue #5); off keeps them as text.
    Display equations take the template's 「수식」 style when it has one, else BODY.
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

    # pull Markdown footnote definitions ([^id]: …) out of the body; the [^id]
    # references stay in the text and become Hangul footnote controls at emit time.
    markdown, fn_defs = extract_footnote_defs(markdown, math=math)

    # strip AI:INSTRUCTION paragraphs (directions, not content)
    inst_style = roles.get(INSTRUCTION)
    if inst_style is not None:
        for p in [p for p in doc.paragraphs if str(p.style_id_ref) == inst_style]:
            p.remove()
            result.instructions_removed += 1

    # insert at a marker if present, else append to the last section
    marker_section, marker = _find_insertion_marker(doc, marker_token)
    target_section = marker_section or doc.sections[-1]
    result.inserted_at_marker = marker is not None
    try:
        result.target_section = doc.sections.index(target_section)
    except ValueError:  # pragma: no cover — sections list should contain the target
        result.target_section = -1
    if marker is None and len(doc.sections) > 1:
        # Appending to the last section is only right when that section is the body.
        # House templates often reserve sections per part (front matter, body,
        # references) with their own headers and page furniture, so silently landing
        # in the last one puts the whole document under the wrong running heads.
        result.warnings.append(
            f"no {BODY_MARKER} marker in the template; content was appended to the "
            f"last of {len(doc.sections)} sections (index {result.target_section}). "
            f"If that section is not the body, put a {BODY_MARKER} paragraph where "
            f"the content belongs."
        )

    # tables generated below copy a template table's format (house style)
    caption_pattern = re.compile(re.escape(table_template), re.I) if table_template else None
    ref_element, ref_section, designated = _find_reference_table(doc, caption_pattern)
    ref_element_copy = copy.deepcopy(ref_element) if ref_element is not None else None
    table_fmt = (
        _table_format(ref_element, _char_height_lookup(doc))
        if ref_element is not None else None
    )

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
            write_text(para, seg.text, char, base_char)

    fn_used: set[str] = set()
    fn_missing: list[str] = []

    # hyperlinks (issue #27): a template 「하이퍼링크」 character style, if any
    hl_char = None
    for i in infos.values():
        if "하이퍼링크" in (i.name or "") or "hyperlink" in (i.name or "").lower():
            try:
                hl_char = doc.style(i.style_id).char_pr_id_ref
            except Exception:
                hl_char = None
            break
    blue_cache: dict = {}

    def link_char(char):
        if blue_links:
            if char not in blue_cache:
                blue_cache[char] = _blue_link_char(doc, char)
            return blue_cache[char]
        return hl_char if hl_char is not None else char

    left_para_cache: dict[str, str] = {}

    def resolve_para(base_para, txt: str):
        if not links or link_align == "keep" or not txt:
            return base_para
        if should_left_align(txt, mode=link_align):
            return _left_para_pr(doc, base_para, left_para_cache)
        return base_para

    def emit_link(para, label: str, url: str, char) -> None:
        from lxml import etree

        xml = link_run_xml(unmask_escapes(label), unmask_escapes(url), link_char(char))
        para.element.append(etree.fromstring(xml))
        result.links += 1

    def emit_text(para, s: str, char) -> None:
        """Add *s* as a run, with each link in it as a hyperlink-field run."""
        pieces = link_pieces(s, unmask=False) if links else [(s, None)]
        if all(u is None for _, u in pieces):
            para.add_run(unmask_escapes(s), char_pr_id_ref=char)
            return

        for t, u in pieces:
            if u is None:
                if t:
                    para.add_run(unmask_escapes(t), char_pr_id_ref=char)
            else:
                emit_link(para, t, u, char)
        if pieces[-1][1] is not None:
            # Hangul draws the paragraph's bullet/number in the *last* run's char, so
            # a paragraph ending in a (blue) link would get a blue bullet — close it
            # with an empty run in the surrounding char
            para.add_run("", char_pr_id_ref=char)

    char_height = _char_height_lookup(doc)
    _eq_count = [sum(len(sec.element.findall(f".//{{{_HP}}}equation")) for sec in doc.sections)]

    def emit_equation(para, latex: str, char) -> None:
        """Append *latex* to *para* as a Hangul equation object (issue #5)."""
        from lxml import etree

        conv = latex_to_hwp(latex)
        for w in conv.warnings:
            msg = f"equation ${latex.strip()}$: {w}"
            if msg not in result.warnings:
                result.warnings.append(msg)
        _eq_count[0] += 1
        base = char_height(char) or 1000
        para.element.append(etree.fromstring(equation_run_xml(
            conv.script, 0x9E000000 + _eq_count[0], char,
            base_unit=base, z_order=_eq_count[0],
        )))
        result.equations += 1

    def write_text(para, text: str, char, ref_char) -> None:
        """Add *text* as runs; ``$…$`` math becomes equation objects (issue #5) and
        the rest goes through :func:`write_plain`."""
        if not math:
            write_plain(para, text, char, ref_char)
            return
        lp = link_pieces(text, unmask=False) if links else [(text, None)]
        if any(u is not None and "$" in t + u for t, u in lp):
            # a link holding a $: links first, so math never splits it apart — a link
            # is one field run, and math in its label stays text (an equation can't
            # sit inside it)
            for t, u in lp:
                if u is None:
                    if t:
                        write_text(para, t, char, ref_char)
                    continue
                if any(k is not None for _, k in math_pieces(t)):
                    msg = f"math in link label [{t}] kept as text"
                    if msg not in result.warnings:
                        result.warnings.append(msg)
                emit_link(para, unescape_dollars(t), u, char)
            if lp[-1][1] is not None:
                para.add_run("", char_pr_id_ref=char)
            return
        pieces = math_pieces(text)
        if len(pieces) == 1 and pieces[0][1] is None:
            write_plain(para, unescape_dollars(text), char, ref_char)
            return
        for t, kind in pieces:
            if kind is None:
                if t:
                    write_plain(para, unescape_dollars(t), char, ref_char)
            else:
                emit_equation(para, t, ref_char)
        if pieces[-1][1] is not None:
            # like links: keep the paragraph's last run a text run in the base char
            para.add_run("", char_pr_id_ref=char)

    def write_plain(para, text: str, char, ref_char) -> None:
        """Add *text* as runs, turning each ``[^id]`` into a footnote control in place.

        Text before/after a reference stays a normal run (in *char*); the reference
        run uses *ref_char* (the paragraph's base char, so the mark isn't bold just
        because it sits in a bold span). A reference with no definition is left as
        literal text and reported. Shared by body paragraphs and table cells."""
        last = 0
        for m in _FOOTNOTE_REF_RE.finditer(text):
            fid = m.group(1)
            if fid not in fn_defs:
                if fid not in fn_missing:
                    fn_missing.append(fid)
                continue  # leave the literal [^id]
            if m.start() > last:
                emit_text(para, text[last : m.start()], char)
            _emit_footnote(para, fn_defs[fid], ref_char)
            fn_used.add(fid)
            last = m.end()
        if last < len(text) or last == 0:
            emit_text(para, text[last:], char)

    body_style = roles.get("BODY")
    body_para = infos[body_style].para_pr_id if body_style in infos else None

    # footnote wiring (issue #23): the note paragraph's style/char, a continuous
    # number seed (so chained parts keep counting), and a unique instId generator.
    fn_sid, fn_para, fn_char = _footnote_style(doc, infos, body_style, body_para)
    _fn_count = [_count_footnotes(doc)]  # continue numbering past existing footnotes
    fn_format = _section_footnote_format(target_section)
    if fn_defs and not _section_has_footnote_pr(target_section):
        result.warnings.append(
            "template section has no <hp:footNotePr>; footnotes may not number/format "
            "as expected — add footnote settings to the section (한글: 입력>각주)."
        )

    def _emit_footnote(para, note_text: str, body_char) -> None:
        _fn_count[0] += 1
        # Hangul's own notes start with a space after the auto-number ("1) 기관별로…")
        note = " " + note_text.strip()
        if links:
            result.links += sum(1 for _, u in link_pieces(note) if u is not None)
        effective_fn_para = resolve_para(fn_para, note_text)
        run_el = _build_footnote_run(
            _fn_count[0], 0xA0000000 + _fn_count[0], note,
            fn_sid, effective_fn_para, fn_char, body_char, fn_format,
            links=links, link_char=link_char(fn_char) if links else None,
        )
        para.element.append(run_el)

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

    blocks = parse_markdown(markdown, math=math)
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
                ref_element_copy, chapter_label(), equal_columns=equal_columns,
                text_writer=write_text,
                para_resolver=resolve_para,
                run_writer=add_runs,
            )
            place(table.paragraph.element)
            result.placed += 1
            continue

        if block.kind == "math":
            eq_sid = body_style
            for i in infos.values():
                if "수식" in (i.name or "") or "equation" in (i.name or "").lower():
                    eq_sid = i.style_id
                    break
            eq_para_pr = infos[eq_sid].para_pr_id if eq_sid in infos else body_para
            eq_char = doc.style(eq_sid).char_pr_id_ref if eq_sid is not None else None
            eq_para = target_section.add_paragraph(
                "", style_id_ref=eq_sid, para_pr_id_ref=eq_para_pr, include_run=False
            )
            emit_equation(eq_para, block.text, eq_char)
            eq_para.add_run("", char_pr_id_ref=eq_char)
            place(eq_para.element)
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

        info = infos.get(style_id)
        if block.kind == "heading":
            text = _heading_render_text(block, info)
        elif block.kind == "bullet":
            text = _bullet_render_text(block, role, info)
        else:
            text = block.text

        effective_para = resolve_para(para_pr, text)
        # build the paragraph empty, then add one run per inline segment so
        # **bold** / *italic* become real runs (bold uses the same font family's
        # bold weight via ensure_run_style); plain text is a single run.
        para = target_section.add_paragraph(
            "", style_id_ref=style_id, para_pr_id_ref=effective_para, include_run=False
        )
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

    # footnote bookkeeping: a [^id] without a definition stays literal text, and a
    # definition nothing references is dropped — both are almost always typos
    if fn_missing:
        result.warnings.append(
            "footnote reference(s) without a definition left as text: "
            + ", ".join(f"[^{f}]" for f in fn_missing)
        )
    unused = [f for f in fn_defs if f not in fn_used]
    if unused:
        result.warnings.append(
            "footnote definition(s) never referenced (dropped): "
            + ", ".join(f"[^{f}]" for f in unused)
        )

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

    # single-part write into a multi-marker template: don't leave the other
    # markers ({{intro}}, {{references}}, …) sitting in the output as literal
    # text. fill_sections keeps this off and strips once, after all parts land.
    if strip_other_markers:
        n = _strip_unfilled_markers(doc)
        if n:
            result.warnings.append(
                f"stripped {n} unfilled marker(s) (no content supplied for them)"
            )

    # Clean template reference elements, update preview parts, and audit leftover tokens
    # once the final markers and content have settled.
    if strip_table_token:
        _drop_reference_tables(doc)
        _drop_figure_template_paragraphs(doc)
        if designated and ref_element is not None and ref_element.getparent() is not None:
            _strip_table_token(ref_element)
            ref_section.mark_dirty()
        _update_preview_parts(doc)
        leftovers = _find_residual_tokens(doc)
        if leftovers:
            toks = ", ".join(sorted(set(leftovers)))
            result.warnings.append(
                f"unconsumed template marker(s) left in document: {toks}"
            )

    doc.save_to_path(str(output or template))
    return result


def fill_sections(
    template: Path | str,
    parts: list[tuple[str, str]],
    *,
    output: Path | str | None = None,
    chapter: str | int | None = None,
    table_template: str | None = None,
    equal_columns: bool = False,
    links: bool = True,
    blue_links: bool = False,
    math: bool = True,
    link_align: str = "auto",
) -> AuthorResult:
    """Fill several named markers, each from its own Markdown part.

    *parts* is ``[(marker_token, markdown), …]`` — e.g. ``[("{{summary}}", …),
    ("{{body}}", …), ("{{references}}", …)]``. Each part is placed at its own
    marker (so 요약·들어가며·본문·참고문헌 land in their own sections with their own
    running heads), consuming that marker. Implemented by chaining
    :func:`fill_from_markdown` so its block/table/cross-ref logic is reused
    unchanged. The aggregate result sums the per-part counts and warnings.
    """
    import tempfile

    agg = AuthorResult()
    if not parts:
        return agg
    final_out = Path(output) if output else Path(template)
    tmp_dir = Path(tempfile.mkdtemp(prefix="fill-sections-"))
    try:
        src: Path | str = template
        last = len(parts) - 1
        for i, (token, markdown) in enumerate(parts):
            dest = final_out if i == last else tmp_dir / f"step{i}.hwpx"
            res = fill_from_markdown(
                src,
                markdown,
                output=dest,
                chapter=chapter,
                table_template=table_template,
                marker_token=token,
                equal_columns=equal_columns,
                strip_table_token=(i == last),
                links=links,
                blue_links=blue_links,
                math=math,
                link_align=link_align,
            )
            agg.placed += res.placed
            agg.links += res.links
            agg.equations += res.equations
            agg.instructions_removed += res.instructions_removed
            agg.unmapped_roles += [
                r for r in res.unmapped_roles if r not in agg.unmapped_roles
            ]
            agg.warnings += [f"[{token}] {w}" for w in res.warnings]
            src = dest
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    # consume markers for parts that weren't supplied, so no literal {{x}} survives
    doc = HwpxDocument.open(str(final_out))
    n = _strip_unfilled_markers(doc)
    _drop_reference_tables(doc)
    _drop_figure_template_paragraphs(doc)
    _update_preview_parts(doc)
    leftovers = _find_residual_tokens(doc)
    if leftovers:
        toks = ", ".join(sorted(set(leftovers)))
        agg.warnings.append(
            f"unconsumed template marker(s) left in document: {toks}"
        )
    doc.save_to_path(str(final_out))
    if n:
        agg.warnings.append(f"stripped {n} unfilled marker(s) not supplied by this build")
    return agg


def build_report(
    manifest: Path | str,
    *,
    output: Path | str | None = None,
    links: bool | None = None,
    blue_links: bool | None = None,
    math: bool | None = None,
    link_align: str | None = None,
) -> AuthorResult:
    """Assemble a multi-section report from a JSON manifest into one template.

    The manifest (paths relative to its own directory) names the template and the
    Markdown for each part; parts are placed at their markers via
    :func:`fill_sections` (front-to-back order), so 요약·들어가며·본문·참고문헌 land in
    their own sections. A part may be a single file or a list (concatenated)::

        {
          "template": "전략과제.normalized.hwpx",
          "output": "최종보고서.hwpx",
          "chapter": null,
          "parts": {
            "summary": "src/00_요약.md",
            "intro": "들어가며.md",
            "body": ["src/01_서론.md", "src/02_동향.md"],
            "references": "src/09_참고문헌.md"
          }
        }

    A part whose marker isn't in the template falls back (append to last section)
    with a warning — surfaced in the result, prefixed by the marker.

    Optional manifest keys ``"links"`` (default true) / ``"blue_links"`` mirror the
    ``write`` options, as does ``"math"`` (default true); the *links* /
    *blue_links* / *math* arguments override them.
    """
    import json

    mpath = Path(manifest)
    data = json.loads(mpath.read_text(encoding="utf-8"))
    base = mpath.parent

    def _resolve(p: str) -> str:
        return p if Path(p).is_absolute() else str(base / p)

    template = _resolve(data["template"])
    parts_cfg = data.get("parts", {})
    part_order = ("summary", "intro", "body", "references", "appendix")
    parts: list[tuple[str, str]] = []
    for name in part_order:
        if name not in parts_cfg:
            continue
        files = parts_cfg[name]
        if isinstance(files, str):
            files = [files]
        md = "\n\n".join(
            Path(_resolve(f)).read_text(encoding="utf-8") for f in files
        )
        parts.append(("{{" + name + "}}", md))
    if not parts:
        raise ValueError("manifest has no 'parts' to assemble")

    out = output if output is not None else data.get("output")
    if out is not None and not Path(out).is_absolute():
        out = str(base / out)
    return fill_sections(
        template,
        parts,
        output=out,
        chapter=data.get("chapter"),
        table_template=data.get("table_template"),
        equal_columns=data.get("equal_columns", False),
        links=data.get("links", True) if links is None else links,
        blue_links=data.get("blue_links", False) if blue_links is None else blue_links,
        math=data.get("math", True) if math is None else math,
        link_align=data.get("link_align", "auto") if link_align is None else link_align,
    )
