"""Normalize a flat real-world template into an hwp-agent-friendly one.

Most real 서식 are "flat" (backlog item H): the numbering ladder exists only as
plain styles (로마자 / "1." / "1)" — no ``<hh:heading type="OUTLINE">``), and
bullet siblings collide on outline level so the role map drops some. This module
detects the ladder candidates and declares them via the ``AI:<ROLE>`` naming
convention (docs/template-convention.md) — the same fix ``check`` prescribes for
manual application in Hangul, automated:

* ``plan_normalization`` (read-only) — propose ``AI:HEADING_n`` / ``AI:BULLET_n``
  declarations with a per-style rationale; ambiguous ladders are *reported, not
  guessed* (size ties, duplicate enumerator classes, mixed outline systems).
* ``apply_normalization`` — write the declarations into the ``engName`` attribute
  of ``Contents/header.xml`` by targeted byte substitution (the Korean ``name``
  a human sees in Hangul stays untouched), repacked with the original container
  preserved (:mod:`.container`) so Hangul (보안수준 '높음') accepts the file.

The declared file then classifies as structured and ``write`` maps onto it. The
heuristics take plain :class:`~.styles.StyleInfo` lists + lookups so doctor's
item-G fallback can reuse them.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

from hwpx.document import HwpxDocument

from .container import _rewrite_zip_preserving
from .doctor import _size_lookup
from .styles import (
    StyleInfo,
    _role_override,
    bullet_glyph_name,
    bullet_glyph_rank,
    classify_document,
    enumerator_class,
    read_style_system,
    role_map,
)

MAX_HEADING_LEVELS = 6

_ENUM_CLASS_KO = {
    "ROMAN": "로마자류",
    "DECIMAL_DOT": "'1.'류",
    "DECIMAL_PAREN": "'1)'류",
    "CIRCLED": "원문자류",
    "HANGUL": "한글류",
    "LATIN": "알파벳류",
}


@dataclass
class NormalizeAction:
    """One planned declaration: set ``engName`` of style *style_id* to *declaration*."""

    style_id: str
    name: str
    old_eng_name: str
    role: str  # e.g. "HEADING_1"
    declaration: str  # e.g. "AI:HEADING_1"
    size: float | None
    rationale: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class NormalizeSkip:
    """A style we looked at but deliberately did not declare, with the reason."""

    style_id: str
    name: str
    size: float | None
    use_count: int
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class NormalizePlan:
    file: str
    actions: list[NormalizeAction] = field(default_factory=list)
    skipped: list[NormalizeSkip] = field(default_factory=list)
    already_declared: list[dict] = field(default_factory=list)
    classification_before: str = "flat"
    classification_expected: str = "flat"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "actions": [a.as_dict() for a in self.actions],
            "skipped": [s.as_dict() for s in self.skipped],
            "already_declared": self.already_declared,
            "classification_before": self.classification_before,
            "classification_expected": self.classification_expected,
            "warnings": self.warnings,
        }


def propose_heading_ladder(
    infos: list[StyleInfo],
    size_of,
    *,
    body_id: str | None,
) -> tuple[list[NormalizeAction], list[NormalizeSkip], list[str]]:
    """Pick plain enumerator styles as ``HEADING_1..n`` by font size descending.

    Conservative: any ambiguity (size tie, duplicate enumerator class, a *used*
    OUTLINE style already present, fewer than 2 candidates) aborts the whole
    ladder with a warning — report, don't guess.
    """
    warnings: list[str] = []
    skips: list[NormalizeSkip] = []

    used_outline = [
        i for i in infos if i.heading_type == "OUTLINE" and i.use_count > 0
    ]
    if used_outline:
        names = ", ".join(f"'{i.name}'" for i in used_outline[:3])
        warnings.append(
            f"사용 중인 OUTLINE 스타일이 이미 있음({names}) — 혼합 번호 체계는 "
            "자동 선언하지 않습니다. 한글에서 직접 정리하세요."
        )
        return [], skips, warnings

    body_size = size_of(body_id) if body_id is not None else None
    candidates: list[tuple[StyleInfo, float, str]] = []
    for i in infos:
        if i.style_type != "PARA" or i.heading_type != "NONE":
            continue
        if body_id is not None and i.style_id == body_id:
            continue
        if i.use_count <= 0:
            continue
        cls = enumerator_class(i.name)
        if cls is None:
            continue
        size = size_of(i.style_id)
        if size is None:
            skips.append(
                NormalizeSkip(
                    i.style_id, i.name, None, i.use_count,
                    "글자 크기를 확인할 수 없어 사다리 순서를 정할 수 없음",
                )
            )
            continue
        if body_size is not None and size <= body_size:
            skips.append(
                NormalizeSkip(
                    i.style_id, i.name, size, i.use_count,
                    f"본문({body_size}pt)보다 크지 않아 헤딩 후보에서 제외",
                )
            )
            continue
        candidates.append((i, size, cls))

    if len(candidates) < 2:
        if candidates:
            i, size, _ = candidates[0]
            skips.append(
                NormalizeSkip(
                    i.style_id, i.name, size, i.use_count,
                    "헤딩 후보가 1개뿐 — 2단 이상이어야 사다리로 선언",
                )
            )
        return [], skips, warnings

    candidates.sort(key=lambda c: -c[1])

    classes = [cls for _, _, cls in candidates]
    if len(set(classes)) != len(classes):
        dup = next(c for c in classes if classes.count(c) > 1)
        warnings.append(
            f"번호 모양({_ENUM_CLASS_KO.get(dup, dup)})이 겹치는 헤딩 후보가 "
            "있어 사다리 순서를 단정할 수 없음 — 자동 선언 생략."
        )
        return [], skips, warnings
    sizes = [size for _, size, _ in candidates]
    if len(set(sizes)) != len(sizes):
        warnings.append(
            "글자 크기가 같은 헤딩 후보가 있어 사다리 순서를 단정할 수 없음 — "
            "자동 선언 생략."
        )
        return [], skips, warnings

    actions: list[NormalizeAction] = []
    for n, (i, size, cls) in enumerate(candidates[:MAX_HEADING_LEVELS], start=1):
        actions.append(
            NormalizeAction(
                style_id=i.style_id,
                name=i.name,
                old_eng_name=i.eng_name,
                role=f"HEADING_{n}",
                declaration=f"AI:HEADING_{n}",
                size=size,
                rationale=f"번호 모양 {_ENUM_CLASS_KO.get(cls, cls)}, 크기 {n}순위 ({size}pt)",
            )
        )
    for i, size, _ in candidates[MAX_HEADING_LEVELS:]:
        skips.append(
            NormalizeSkip(
                i.style_id, i.name, size, i.use_count,
                f"헤딩 사다리 최대 {MAX_HEADING_LEVELS}단 초과",
            )
        )
    return actions, skips, warnings


def propose_bullet_ladder(
    infos: list[StyleInfo],
    size_of,
    glyph_of,
) -> tuple[list[NormalizeAction], list[NormalizeSkip], list[str]]:
    """Order bullet styles as ``BULLET_1..n`` by glyph class, then size.

    HWP encodes bullet nesting by the *glyph*, not the outline level (item G):
    squares > circles > dashes > dots, font size descending as tiebreak. A tie on
    both aborts the ladder. ``glyph_of(style_id)`` returns the bullet char from
    the header (may be an unclassifiable PUA codepoint — then the style *name*'s
    first character is tried).

    Candidates are used styles with a BULLET definition, plus used plain styles
    whose *name* is a single bullet glyph — the JI convention for manual bullet
    heads (their marker is literal text; author re-supplies it on write).
    """
    warnings: list[str] = []
    skips: list[NormalizeSkip] = []

    candidates: list[tuple[StyleInfo, float | None, int, str]] = []
    for i in infos:
        if i.style_type != "PARA" or i.use_count <= 0:
            continue
        if i.heading_type == "BULLET":
            glyph = (glyph_of(i.style_id) or "").strip()
            rank = bullet_glyph_rank(glyph)
            if rank == bullet_glyph_rank(""):  # unclassified → fall back to the name
                name_char = (i.name or "").strip()[:1]
                name_rank = bullet_glyph_rank(name_char)
                if name_rank < rank:
                    rank, glyph = name_rank, name_char
        elif i.heading_type == "NONE" and (named := bullet_glyph_name(i.name)):
            glyph, rank = named, bullet_glyph_rank(named)
        else:
            continue
        candidates.append((i, size_of(i.style_id), rank, glyph or "?"))

    if not candidates:
        return [], skips, warnings

    def key(c: tuple[StyleInfo, float | None, int, str]):
        i, size, rank, _ = c
        return (
            rank,
            -(size or 0.0),
            i.outline_level if i.outline_level is not None else 0,
            -i.use_count,
            int(i.style_id) if i.style_id.isdigit() else 0,
        )

    candidates.sort(key=key)

    for a, b in zip(candidates, candidates[1:], strict=False):
        if a[2] == b[2] and a[1] == b[1]:
            warnings.append(
                f"글머리 스타일 '{a[0].name}'와(과) '{b[0].name}'이(가) 글리프 "
                "서열·크기 모두 같아 사다리 순서를 단정할 수 없음 — 자동 선언 생략."
            )
            return [], skips, warnings

    actions = [
        NormalizeAction(
            style_id=i.style_id,
            name=i.name,
            old_eng_name=i.eng_name,
            role=f"BULLET_{n}",
            declaration=f"AI:BULLET_{n}",
            size=size,
            rationale=(
                f"글머리 글리프 '{glyph}' 서열 {rank}, {size}pt"
                + (
                    " — 이름 글리프 일반 스타일 (수동 불릿 관행, 글머리표는 본문에 직접 입력됨)"
                    if i.heading_type == "NONE"
                    else ""
                )
            ),
        )
        for n, (i, size, rank, glyph) in enumerate(candidates, start=1)
    ]
    return actions, skips, warnings


def plan_normalization(path: str | Path) -> NormalizePlan:
    """Read-only: propose the ``AI:`` declarations for a flat template."""
    doc = HwpxDocument.open(str(path))
    infos = read_style_system(doc)
    size_of = _size_lookup(doc)
    body_id = role_map(doc).get("BODY")

    def glyph_of(style_id: str) -> str | None:
        info = next((i for i in infos if i.style_id == style_id), None)
        if info is None or info.para_pr_id is None:
            return None
        pp = doc.paragraph_property(info.para_pr_id)
        heading = pp.heading if pp else None
        if heading is None or heading.id_ref is None:
            return None
        bullet = doc.bullet(heading.id_ref)
        return bullet.char if bullet is not None else None

    plan = NormalizePlan(file=str(path))
    plan.classification_before = classify_document(doc)

    for i in infos:
        role = _role_override(i)
        if role:
            plan.already_declared.append(
                {"style_id": i.style_id, "name": i.name, "role": role}
            )

    declared_roles = {d["role"] for d in plan.already_declared}

    # all-or-nothing per ladder: partially declared ladders are a human's work
    # in progress — completing them by guesswork could contradict the intent.
    if any(r.startswith("HEADING_") for r in declared_roles):
        plan.warnings.append(
            "AI:HEADING_n 선언이 이미 있어 헤딩 사다리는 손대지 않습니다."
        )
        h_actions: list[NormalizeAction] = []
    else:
        h_actions, h_skips, h_warnings = propose_heading_ladder(
            infos, size_of, body_id=body_id
        )
        plan.skipped.extend(h_skips)
        plan.warnings.extend(h_warnings)

    if any(r.startswith("BULLET_") for r in declared_roles):
        plan.warnings.append(
            "AI:BULLET_n 선언이 이미 있어 글머리 사다리는 손대지 않습니다."
        )
        b_actions: list[NormalizeAction] = []
    else:
        b_actions, b_skips, b_warnings = propose_bullet_ladder(
            infos, size_of, glyph_of
        )
        plan.skipped.extend(b_skips)
        plan.warnings.extend(b_warnings)

    plan.actions = h_actions + b_actions

    declared_heading_levels = {
        int(m.group(1))
        for r in declared_roles | {a.role for a in plan.actions}
        if (m := re.fullmatch(r"HEADING_(\d+)", r))
    }
    plan.classification_expected = (
        "structured"
        if len(declared_heading_levels) >= 2
        else plan.classification_before
    )
    return plan


_STYLE_TAG_RE = re.compile(r"<(?:[A-Za-z0-9]+:)?style\b[^>]*?/?>")
_ID_ATTR_RE = re.compile(r'\bid\s*=\s*"([^"]*)"')
_ENGNAME_ATTR_RE = re.compile(r'\bengName\s*=\s*"[^"]*"')


def _declare_in_header(header_xml: str, actions: list[NormalizeAction]) -> str:
    """Set ``engName`` on each action's ``<hh:style>`` tag by span substitution.

    Touches nothing but the targeted attribute values, so the header stays
    byte-identical elsewhere. Raises if a style id doesn't match exactly one tag.
    """
    spans: dict[str, tuple[int, int, str]] = {}  # style_id -> (start, end, tag)
    for m in _STYLE_TAG_RE.finditer(header_xml):
        idm = _ID_ATTR_RE.search(m.group(0))
        if not idm:
            continue
        sid = idm.group(1)
        if sid in spans:
            raise ValueError(f"header.xml: style id {sid!r}인 태그가 여러 개")
        spans[sid] = (m.start(), m.end(), m.group(0))

    edits: list[tuple[int, int, str]] = []
    for action in actions:
        if action.style_id not in spans:
            raise ValueError(
                f"header.xml: style id {action.style_id!r} 태그를 찾지 못함"
            )
        start, end, tag = spans[action.style_id]
        attr = f"engName={quoteattr(action.declaration)}"
        if _ENGNAME_ATTR_RE.search(tag):
            new_tag = _ENGNAME_ATTR_RE.sub(attr, tag, count=1)
        else:
            closer = "/>" if tag.endswith("/>") else ">"
            new_tag = tag[: -len(closer)].rstrip() + f" {attr}{closer}"
        edits.append((start, end, new_tag))

    out = header_xml
    for start, end, new_tag in sorted(edits, reverse=True):
        out = out[:start] + new_tag + out[end:]
    return out


def apply_normalization(
    path: str | Path, plan: NormalizePlan, output: str | Path
) -> None:
    """Write *plan*'s declarations into a container-preserving copy at *output*."""
    if not plan.actions:
        raise ValueError("적용할 선언이 없습니다 (plan.actions가 비어 있음)")

    with zipfile.ZipFile(path) as zf:
        headers = [n for n in zf.namelist() if n.endswith("header.xml")]
        if len(headers) != 1:
            raise ValueError(f"header.xml 후보가 {len(headers)}개: {headers}")
        header_name = headers[0]
        header_xml = zf.read(header_name).decode("utf-8")

    new_xml = _declare_in_header(header_xml, plan.actions)
    new_bytes = new_xml.encode("utf-8")

    # sanity: still well-formed, and every declaration is findable
    ElementTree.fromstring(new_bytes)
    for action in plan.actions:
        if f'engName="{action.declaration}"' not in new_xml:
            raise ValueError(f"선언 검증 실패: {action.declaration}")

    _rewrite_zip_preserving(path, output, {header_name: new_bytes})
