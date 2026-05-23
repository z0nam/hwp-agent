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
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


@dataclass
class Block:
    kind: str  # "heading" | "paragraph" | "bullet"
    level: int  # heading level 1-6, bullet nesting 1+, 0 for paragraph
    text: str


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


def _inline(text: str) -> str:
    """Flatten inline emphasis to plain text (first cut)."""
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    return text


def parse_markdown(markdown: str) -> list[Block]:
    """Parse Markdown into headings, paragraphs, and bullets (minimal)."""
    blocks: list[Block] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("paragraph", 0, _inline(" ".join(para))))
            para.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if m := _HEADING_RE.match(line):
            flush()
            blocks.append(Block("heading", len(m.group(1)), _inline(m.group(2).strip())))
        elif m := _BULLET_RE.match(line):
            flush()
            level = len(m.group(1).expandtabs(2)) // 2 + 1
            blocks.append(Block("bullet", level, _inline(m.group(2).strip())))
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
    max_heading = max(
        (int(r.split("_")[1]) for r in roles if r.startswith("HEADING_")), default=0
    )
    max_bullet = max(
        (int(r.split("_")[1]) for r in roles if r.startswith("BULLET_")), default=0
    )

    def resolve(block: Block) -> tuple[str | None, str]:
        if block.kind == "heading" and max_heading:
            role = f"HEADING_{min(block.level, max_heading)}"
        elif block.kind == "bullet" and max_bullet:
            role = f"BULLET_{min(block.level, max_bullet)}"
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
        para = target_section.add_paragraph(
            block.text, style_id_ref=style_id, para_pr_id_ref=para_pr
        )
        if marker is not None:  # move the freshly-appended paragraph before the marker
            element = para.element
            element.getparent().remove(element)
            marker.element.addprevious(element)
        result.placed += 1

    if marker is not None:
        marker.element.getparent().remove(marker.element)

    doc.save_to_path(str(output or template))
    return result
