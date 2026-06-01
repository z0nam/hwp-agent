"""Convert an HWPX document to body-focused Markdown.

The inverse of :func:`hwp_agent.ops.author.fill_from_markdown`: walk the document
in order and emit a Markdown projection of its structure — headings (`#`/`##`/…),
bullet / ordered lists, body paragraphs, and **rectangular tables**. Complex
(merged) tables are flattened by **unmerging cells**: every cell covered by a
``<hp:cellSpan>`` gets the same value duplicated into its slot, so the table fits
Markdown's strict ``| … |`` rectangular shape. Data is preserved exactly; merge
intent is lost (the visible doubled-value pattern signals where merges existed).

This is M6 v1 — the "messy HWP → MD → re-write into a clean template" path. The
v1 scope is deliberately small:

* role detection: try the role map's ``style_id → role`` (HEADING_n / BULLET_n /
  ORDERED_n / BODY) first; fall back to the paragraph property's ``<hh:heading>``
  ``type`` + ``level`` (OUTLINE / BULLET) when the style has no AI role.
* inline emphasis (run-level **bold** / *italic*) is **not** detected — runs come
  out as plain text. Worth doing once an extracted draft actually needs it.
* images: a ``<hp:pic>`` is emitted as the paragraph's caption text only; the
  image bytes aren't extracted (use ``hwp-agent image list`` for that).
* body-only mode (``body_only=True``) skips the front matter (cover, TOC, …) by
  dropping everything before the first level-1 heading.
"""

from __future__ import annotations

from pathlib import Path

from hwpx.document import HwpxDocument

from .styles import role_map

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"


def extract_markdown(path: str | Path, *, body_only: bool = False) -> str:
    """Render *path*'s contents as Markdown. Returns a single ``str``."""
    doc = HwpxDocument.open(str(path))
    role_of = {str(sid): role for role, sid in role_map(path).items()}

    lines: list[str] = []
    started = not body_only  # if body_only, skip until first H1
    for section in doc.sections:
        for paragraph in section.paragraphs:
            tbl = paragraph.element.find(f".//{{{_HP}}}tbl")
            if tbl is not None:
                if started:
                    _emit_table(lines, tbl)
                continue
            role, level = _classify(paragraph, doc, role_of)
            text = _paragraph_text(paragraph)
            if body_only and not started:
                if role == "HEADING" and level == 1:
                    started = True
                else:
                    continue
            _emit_paragraph(lines, role, level, text)
    return _tidy("\n".join(lines))


# --------------------------------------------------------------------------- #
# paragraph classification
# --------------------------------------------------------------------------- #
def _classify(paragraph, doc, role_of: dict[str, str]) -> tuple[str, int]:
    """Return ``(role, level)`` where role ∈ {HEADING, BULLET, ORDERED, BODY}.

    Prefer the AI role map (`AI:<ROLE>` naming + outline-style detection) so a
    template's named style wins. Fall back to the paragraph property's heading
    type/level for documents with no role assignments.
    """
    role = role_of.get(str(paragraph.style_id_ref), "")
    if role.startswith("HEADING_"):
        return "HEADING", int(role.split("_")[1])
    if role.startswith("BULLET_"):
        return "BULLET", int(role.split("_")[1])
    if role.startswith("ORDERED_"):
        return "ORDERED", int(role.split("_")[1])
    if role == "BODY":
        return "BODY", 0
    # fallback: read the paragraph property directly
    pp = doc.paragraph_property(paragraph.para_pr_id_ref)
    h = getattr(pp, "heading", None) if pp else None
    if h is None or not h.type:
        return "BODY", 0
    if h.type == "OUTLINE":
        return "HEADING", int(h.level) + 1
    if h.type == "BULLET":
        return "BULLET", int(h.level) + 1
    return "BODY", 0


def _paragraph_text(paragraph) -> str:
    """All text content of *paragraph*, runs concatenated. v1 = no emphasis."""
    parts: list[str] = []
    for run in paragraph.element.findall(f"{{{_HP}}}run"):
        for t in run.findall(f"{{{_HP}}}t"):
            if t.text:
                parts.append(t.text)
    return "".join(parts).strip()


# --------------------------------------------------------------------------- #
# emitters
# --------------------------------------------------------------------------- #
def _emit_paragraph(lines: list[str], role: str, level: int, text: str) -> None:
    if not text:
        return
    if role == "HEADING":
        lines.append(f"{'#' * max(1, min(level, 6))} {text}")
    elif role == "BULLET":
        lines.append(f"{'  ' * (max(1, level) - 1)}* {text}")
    elif role == "ORDERED":
        lines.append(f"{'  ' * (max(1, level) - 1)}1. {text}")
    else:
        lines.append(text)
    lines.append("")  # blank between blocks


def _emit_table(lines: list[str], tbl) -> None:
    """Render an HWPX table as a rectangular Markdown pipe table.

    Merged cells are flattened — every position covered by a ``<hp:cellSpan>`` is
    filled with the same value, so the resulting grid is strictly rectangular.
    The first row becomes the Markdown header (Markdown requires one).
    """
    row_cnt = int(tbl.get("rowCnt") or 0)
    col_cnt = int(tbl.get("colCnt") or 0)
    if row_cnt == 0 or col_cnt == 0:
        return

    # caption (above the table)
    cap_el = tbl.find(f"{{{_HP}}}caption")
    if cap_el is not None:
        cap_text = " ".join(
            (t.text or "").strip() for t in cap_el.iter(f"{{{_HP}}}t")
        ).strip()
        if cap_text:
            lines.append(cap_text)
            lines.append("")

    grid: list[list[str]] = [[""] * col_cnt for _ in range(row_cnt)]
    for tr in tbl.findall(f"{{{_HP}}}tr"):
        for tc in tr.findall(f"{{{_HP}}}tc"):
            addr = tc.find(f"{{{_HP}}}cellAddr")
            span = tc.find(f"{{{_HP}}}cellSpan")
            row = int(addr.get("rowAddr") or 0) if addr is not None else 0
            col = int(addr.get("colAddr") or 0) if addr is not None else 0
            rs = int(span.get("rowSpan") or 1) if span is not None else 1
            cs = int(span.get("colSpan") or 1) if span is not None else 1
            sub = tc.find(f"{{{_HP}}}subList")
            text = ""
            if sub is not None:
                cell_paras: list[str] = []
                for p in sub.findall(f"{{{_HP}}}p"):
                    paragraph_text = "".join(
                        t.text or "" for t in p.iter(f"{{{_HP}}}t")
                    ).strip()
                    if paragraph_text:
                        cell_paras.append(paragraph_text)
                # Markdown pipe cells are single-line; preserve paragraph breaks
                # with a soft `<br>` so multi-paragraph cells stay readable
                text = "<br>".join(cell_paras)
            # pipe-table cells can't carry literal `|` or newlines
            text = text.replace("|", r"\|").replace("\n", " ")
            for dr in range(rs):
                for dc in range(cs):
                    if row + dr < row_cnt and col + dc < col_cnt:
                        grid[row + dr][col + dc] = text

    def row_md(row: list[str]) -> str:
        return "| " + " | ".join(row) + " |"

    lines.append(row_md(grid[0]))
    lines.append("|" + "|".join(["---"] * col_cnt) + "|")
    for row in grid[1:]:
        lines.append(row_md(row))
    lines.append("")


# --------------------------------------------------------------------------- #
# whitespace tidy — at most one blank line between blocks
# --------------------------------------------------------------------------- #
def _tidy(text: str) -> str:
    out: list[str] = []
    blank = 0
    for line in text.splitlines():
        if line.strip():
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip() + "\n"
