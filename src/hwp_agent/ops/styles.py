"""Read a document's style system, map styles to machine roles, classify its type.

Type-1 ("well-structured") documents drive the AI fill loop: their paragraphs use
real outline styles (Heading 1/2/3 → ``<hh:heading type="OUTLINE" level="N">``),
so Hangul auto-numbers them. We expose that structure as a **role map**
(role → style id) so an authoring layer can reuse the template's own styles.

Role detection is structure-first, naming-second (see docs/template-convention.md):

* **auto** — a style whose paragraph-property heading is ``OUTLINE level=N`` →
  ``HEADING_{N+1}``; ``BULLET`` → ``BULLET_{N+1}``; ``engName="Normal"`` /
  ``name="바탕글"`` → ``BODY``. Canonical ``engName="Outline N"`` is preferred when
  several styles share a level.
* **naming override** — a style whose ``name``/``engName`` matches ``AI:<ROLE>``
  (e.g. ``AI:H1``, ``AI:BODY``, ``AI:INSTRUCTION``) takes that role explicitly.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hwpx.document import HwpxDocument

BODY = "BODY"
INSTRUCTION = "INSTRUCTION"
TITLE = "TITLE"
_AI_ROLE_RE = re.compile(r"^\s*AI:([A-Za-z_][A-Za-z0-9_]*)\s*$")
_ENG_OUTLINE_RE = re.compile(r"^\s*Outline\s+(\d+)\s*$", re.IGNORECASE)
_BODY_NAMES = {"normal", "바탕글"}


@dataclass
class StyleInfo:
    style_id: str
    name: str
    eng_name: str
    para_pr_id: str | None
    heading_type: str  # "NONE" | "OUTLINE" | "BULLET"
    outline_level: int | None  # 0-based, when heading_type in {OUTLINE, BULLET}
    use_count: int = 0  # paragraphs referencing this style

    def as_dict(self) -> dict:
        return {
            "style_id": self.style_id,
            "name": self.name,
            "eng_name": self.eng_name,
            "heading_type": self.heading_type,
            "outline_level": self.outline_level,
            "use_count": self.use_count,
        }


def _role_override(info: StyleInfo) -> str | None:
    for text in (info.name, info.eng_name):
        m = _AI_ROLE_RE.match(text or "")
        if m:
            return m.group(1).upper()
    return None


def read_style_system(doc_or_path: HwpxDocument | Path | str) -> list[StyleInfo]:
    """All paragraph styles with their heading type/level and usage counts."""
    doc = (
        doc_or_path
        if isinstance(doc_or_path, HwpxDocument)
        else HwpxDocument.open(str(doc_or_path))
    )
    usage: Counter[str] = Counter(
        str(p.style_id_ref) for p in doc.paragraphs if p.style_id_ref is not None
    )
    infos: list[StyleInfo] = []
    for sid, st in doc.styles.items():
        pp = doc.paragraph_property(st.para_pr_id_ref)
        heading = pp.heading if pp else None
        infos.append(
            StyleInfo(
                style_id=str(sid),
                name=st.name or "",
                eng_name=st.eng_name or "",
                para_pr_id=str(st.para_pr_id_ref) if st.para_pr_id_ref else None,
                heading_type=(heading.type if heading else "NONE") or "NONE",
                outline_level=heading.level if heading else None,
                use_count=usage.get(str(sid), 0),
            )
        )
    return infos


def _best(candidates: list[StyleInfo], *, prefer_outline_n: int | None = None) -> str:
    """Pick one style id: canonical engName, then most-used, then lowest id."""

    def rank(info: StyleInfo) -> tuple:
        canonical = (
            prefer_outline_n is not None
            and bool(m := _ENG_OUTLINE_RE.match(info.eng_name))
            and int(m.group(1)) == prefer_outline_n
        )
        return (canonical, info.use_count, -int(info.style_id))

    return max(candidates, key=rank).style_id


def role_map(doc_or_path: HwpxDocument | Path | str) -> dict[str, str]:
    """Map machine roles to a single style id (auto-detect + ``AI:<ROLE>``)."""
    infos = read_style_system(doc_or_path)
    roles: dict[str, str] = {}

    # naming overrides win outright
    for info in infos:
        role = _role_override(info)
        if role:
            roles[role] = info.style_id

    by_outline: dict[int, list[StyleInfo]] = {}
    by_bullet: dict[int, list[StyleInfo]] = {}
    body: list[StyleInfo] = []
    for info in infos:
        if info.heading_type == "OUTLINE" and info.outline_level is not None:
            by_outline.setdefault(info.outline_level, []).append(info)
        elif info.heading_type == "BULLET" and info.outline_level is not None:
            by_bullet.setdefault(info.outline_level, []).append(info)
        elif info.name.strip().lower() in _BODY_NAMES or (
            info.eng_name.strip().lower() == "normal"
        ):
            body.append(info)

    for level, cands in by_outline.items():
        roles.setdefault(f"HEADING_{level + 1}", _best(cands, prefer_outline_n=level + 1))
    for level, cands in by_bullet.items():
        roles.setdefault(f"BULLET_{level + 1}", _best(cands))
    if body:
        roles.setdefault(BODY, _best(body))
    return roles


def classify_document(doc_or_path: HwpxDocument | Path | str) -> str:
    """Classify as ``"structured"`` | ``"weak"`` | ``"flat"`` (the 3 doc types)."""
    doc = (
        doc_or_path
        if isinstance(doc_or_path, HwpxDocument)
        else HwpxDocument.open(str(doc_or_path))
    )
    paras = doc.paragraphs
    total = len(paras)
    if total == 0:
        return "flat"

    outline_levels: set[int] = set()
    outline_paras = 0
    for p in paras:
        pp = doc.paragraph_property(p.para_pr_id_ref)
        h = pp.heading if pp else None
        if h and h.type == "OUTLINE":
            outline_paras += 1
            outline_levels.add(h.level)

    if outline_paras >= 3 and len(outline_levels) >= 2:
        return "structured"
    if outline_paras == 0:
        return "flat"
    return "weak"
