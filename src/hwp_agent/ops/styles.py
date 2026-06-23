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
# short heading aliases the docs prescribe (AI:H1 == AI:HEADING_1)
_H_ALIAS_RE = re.compile(r"^H(\d+)$")
_ENG_OUTLINE_RE = re.compile(r"^\s*Outline\s+(\d+)\s*$", re.IGNORECASE)
_BODY_NAMES = {"normal", "바탕글"}
# names that look like an enumerator: "1.", "1)", "가.", "I.", "①" — i.e. a
# numbered-list style rather than a semantic heading.
_ENUM_NAME_RE = re.compile(
    r"^\s*(?:\d+|[IVXivx]+|[A-Za-z]|[가-힣]|[①-⑳Ⅰ-Ⅿ])\s*[.)]?\s*$"
)

# enumerator *classes* — two ladder rungs must differ in class to be a ladder
# (e.g. 로마자 "Ⅰ." > "1." > "1)"); shared by normalize and the item-G fallback.
_ENUM_CLASS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ROMAN", re.compile(r"^(?:[IVXivx]+|[Ⅰ-Ⅿ])\s*[.)]?$")),
    ("DECIMAL_DOT", re.compile(r"^\d+\s*\.$")),
    ("DECIMAL_PAREN", re.compile(r"^\d+\s*\)$")),
    ("CIRCLED", re.compile(r"^[①-⑳]$")),
    ("HANGUL", re.compile(r"^[가-힣]\s*[.)]?$")),
    ("LATIN", re.compile(r"^[A-Za-z]\s*[.)]$")),
)
# descriptive style names seen in real templates that announce a numbering
# scheme without showing the glyph (JI 보고서 서식: name="로마자", 20pt chapter).
_ENUM_NAME_LEXICON = {"로마자": "ROMAN"}

# bullet-glyph nesting order (backlog item G): squares > circles > dashes > dots.
_BULLET_GLYPH_RANKS: tuple[str, ...] = ("■□▣◼￭▪◾", "●○⦁•◦", "-–—―−", "·ㆍ")


def enumerator_class(name: str) -> str | None:
    """Classify a style name as a numbering scheme ("ROMAN", "DECIMAL_DOT", …).

    Returns ``None`` when the name doesn't read as an enumerator. Used to pick
    heading-ladder candidates among plain (non-outline) styles.
    """
    text = (name or "").strip()
    if not text:
        return None
    lex = _ENUM_NAME_LEXICON.get(text)
    if lex:
        return lex
    for cls, pattern in _ENUM_CLASS_PATTERNS:
        if pattern.match(text):
            return cls
    return None


def bullet_glyph_rank(char: str) -> int:
    """Nesting rank of a bullet glyph — lower is shallower (``■`` 0 … ``·`` 3).

    Unrecognized glyphs (e.g. PUA codepoints) rank last (``len(ranks)``)."""
    c = (char or "").strip()[:1]
    for rank, glyphs in enumerate(_BULLET_GLYPH_RANKS):
        if c and c in glyphs:
            return rank
    return len(_BULLET_GLYPH_RANKS)


def bullet_glyph_name(name: str) -> str | None:
    """The glyph when a style *name* is a single bullet glyph ("-", "￭ ") — else None.

    JI 관행: 글리프 한 글자를 그대로 이름으로 쓴 일반(NONE) 스타일을 수동 불릿
    대가리로 사용한다. Such a style's marker is literal text, not a BULLET
    definition — normalize ladders it and author re-supplies the glyph on write.
    """
    text = (name or "").strip()
    if len(text) == 1 and bullet_glyph_rank(text) < len(_BULLET_GLYPH_RANKS):
        return text
    return None


@dataclass
class StyleInfo:
    style_id: str
    name: str
    eng_name: str
    para_pr_id: str | None
    heading_type: str  # "NONE" | "OUTLINE" | "BULLET"
    outline_level: int | None  # 0-based, when heading_type in {OUTLINE, BULLET}
    use_count: int = 0  # paragraphs referencing this style
    style_type: str = "PARA"  # "PARA" | "CHAR" (hh:style @type)

    def as_dict(self) -> dict:
        return {
            "style_id": self.style_id,
            "name": self.name,
            "eng_name": self.eng_name,
            "heading_type": self.heading_type,
            "outline_level": self.outline_level,
            "use_count": self.use_count,
            "style_type": self.style_type,
        }


def _role_override(info: StyleInfo) -> str | None:
    for text in (info.name, info.eng_name):
        m = _AI_ROLE_RE.match(text or "")
        if m:
            role = m.group(1).upper()
            # docs prescribe AI:H1/AI:H2 — alias to the role author consumes
            if alias := _H_ALIAS_RE.match(role):
                return f"HEADING_{alias.group(1)}"
            return role
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
                style_type=(st.type or "PARA").upper(),
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
        heading = _best(cands, prefer_outline_n=level + 1)
        roles.setdefault(f"HEADING_{level + 1}", heading)
        # an enumerator-named outline style at this level (other than the chosen
        # heading) is a numbered-list style → ORDERED_{level+1}.
        ordered = [
            c for c in cands if c.style_id != heading and _ENUM_NAME_RE.match(c.name)
        ]
        if ordered:
            roles.setdefault(f"ORDERED_{level + 1}", _best(ordered))
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

    # a declared ladder (AI:HEADING_n on ≥2 levels) is structured even with no
    # outline paragraphs: author runs on role_map, not on paragraph counts.
    declared_levels = {
        int(m.group(1))
        for info in read_style_system(doc)
        if (role := _role_override(info))
        and (m := re.fullmatch(r"HEADING_(\d+)", role))
    }
    if len(declared_levels) >= 2:
        return "structured"

    if outline_paras == 0:
        return "flat"
    return "weak"
