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


def parse_markdown(markdown: str) -> list[Block]:
    """Parse Markdown into headings, paragraphs, and bullets (minimal).

    Inline ``**bold**`` / ``*italic*`` markers are kept in ``Block.text`` and
    resolved into runs at fill time via :func:`inline_segments`.
    """
    blocks: list[Block] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("paragraph", 0, " ".join(para)))
            para.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if m := _HEADING_RE.match(line):
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
    flush()
    return blocks


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

    for block in parse_markdown(markdown):
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
        for seg in inline_segments(block.text):
            if seg.bold or seg.italic:
                char = doc.ensure_run_style(
                    bold=seg.bold, italic=seg.italic, base_char_pr_id=base_char
                )
            else:
                char = base_char
            para.add_run(seg.text, char_pr_id_ref=char)

        if marker is not None:  # move the freshly-built paragraph before the marker
            element = para.element
            element.getparent().remove(element)
            marker.element.addprevious(element)
        result.placed += 1

    if marker is not None:
        marker.element.getparent().remove(marker.element)

    doc.save_to_path(str(output or template))
    return result
