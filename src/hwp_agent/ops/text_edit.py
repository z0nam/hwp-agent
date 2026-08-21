"""Insert paragraphs into an **already type-set** HWPX, in place.

``write`` fills a template from Markdown and owns the whole body. Once a human has
opened the result in Hangul and spent hours on it — captions, table widths, page
breaks — regenerating throws that away. A one-line correction should not cost a
re-typeset, so this module adds paragraphs to a finished document and touches
nothing else.

The rules below are the ones that make Hangul accept the result:

* **Additive only, container preserved.** Only the target section's bytes are
  rewritten; the ZIP is re-emitted entry-by-entry with the original ``ZipInfo``,
  order and compression, ``mimetype`` first and ``STORED`` (see :mod:`.container`).
  A full re-zip — what ``HwpxDocument.save_to_path`` does — trips Hangul's 보안경고.
  For the same reason the section is edited as **text**, not through the DOM.
* **Style comes from a sibling, not from a role.** A hand-edited document's styles
  are whatever the human settled on, so declared ``AI:*`` roles (if any) no longer
  describe it. Each new paragraph clones the ``paraPrIDRef``/``styleIDRef``/
  ``charPrIDRef`` of a neighbouring paragraph at the same outline depth, where depth
  is read from the paragraph's left indent. That is what keeps an insertion
  indistinguishable from the text around it.
* **Line-layout cache.** Body paragraphs get no ``<hp:linesegarray>`` — Hangul
  computes one on open. Existing paragraphs **keep theirs**: an outline heading whose
  paragraph lacks that cache is demoted to body text (the same hazard
  :func:`hwp_agent.ops.author._lineseg_index` guards against), so stripping the
  section wholesale would silently flatten every heading in it.
* **Anchors must be unique.** A substring that matches two paragraphs is an error,
  not a coin toss — ``find_anchors`` exists so the caller can see the candidates.

Out of scope for now: headings and tables. Both change numbering that the rest of the
document (outline, list of tables, cross-references) already agreed on, so they belong
to a re-typeset via ``write``.
"""

from __future__ import annotations

import random
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .author import Block, TableBlock, parse_markdown
from .container import _read_text, _rewrite_zip_preserving

_HH = "http://www.hancom.co.kr/hwpml/2011/head"

_SECTION_RE = re.compile(r"Contents/section\d+\.xml$")
_P_RE = re.compile(r"<hp:p\b.*?</hp:p>", re.S)
_T_RE = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)
_RUN_RE = re.compile(r"<hp:run\b.*?</hp:run>", re.S)
_LINESEG_RE = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray>", re.S)
_OPEN_RE = re.compile(r"<hp:p\b[^>]*>")
_IDS_RE = re.compile(r'paraPrIDRef="(\d+)"\s+styleIDRef="(\d+)"')
_CHAR_RE = re.compile(r'charPrIDRef="(\d+)"')

#: how far to look either side of the anchor when learning the depth ladder
_NEIGHBOURHOOD = 40


# --------------------------------------------------------------------------- #
# data carriers
# --------------------------------------------------------------------------- #
@dataclass
class Anchor:
    """A paragraph that matched the caller's anchor text."""

    section: str  # section part name
    index: int  # paragraph index within the section
    text: str  # full paragraph text
    para_pr: str
    style_id: str
    char_pr: str
    depth: int  # outline depth inferred from the left indent (1 = outermost)


@dataclass
class InsertResult:
    output: Path
    anchor: Anchor
    where: str  # "before" | "after"
    inserted: int = 0
    warnings: list[str] = field(default_factory=list)


class TextEditError(RuntimeError):
    """Raised when an edit cannot be applied safely."""


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _para_text(paragraph: str) -> str:
    return "".join(_T_RE.findall(paragraph))


def _ids(paragraph: str) -> tuple[str, str, str]:
    m = _IDS_RE.search(paragraph)
    c = _CHAR_RE.search(paragraph)
    if not m:
        return ("", "", "")
    return (m.group(1), m.group(2), c.group(1) if c else "")


def _indent_by_para_pr(header_xml: str) -> dict[str, int]:
    """Map paraPr id → left indent (HWPUNIT), used to rank outline depth."""
    out: dict[str, int] = {}
    for m in re.finditer(r"<hh:paraPr\b[^>]*\bid=\"(\d+)\".*?</hh:paraPr>", header_xml, re.S):
        body = m.group(0)
        left = 0
        margin = re.search(r"<hh:margin>.*?</hh:margin>", body, re.S)
        if margin:
            ml = re.search(r'<hc:left\b[^>]*\bvalue="(-?\d+)"', margin.group(0))
            if ml:
                left = int(ml.group(1))
            mi = re.search(r'<hc:intent\b[^>]*\bvalue="(-?\d+)"', margin.group(0))
            if mi:
                left += max(0, int(mi.group(1)))
        out[m.group(1)] = left
    return out


def _sections(path: str | Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {
            name: _read_text(zf, name)
            for name in zf.namelist()
            if _SECTION_RE.match(name)
        }


def _header(path: str | Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return _read_text(zf, "Contents/header.xml")


def find_anchors(path: str | Path, needle: str) -> list[Anchor]:
    """Every paragraph whose text contains *needle*, in document order."""
    header = _header(path)
    indents = _indent_by_para_pr(header)
    found: list[Anchor] = []
    for name in sorted(_sections(path)):
        paragraphs = _P_RE.findall(_sections(path)[name])
        ladder = _depth_ladder(paragraphs, indents)
        for i, p in enumerate(paragraphs):
            text = _para_text(p)
            if needle in text:
                para_pr, style_id, char_pr = _ids(p)
                found.append(
                    Anchor(
                        section=name,
                        index=i,
                        text=text,
                        para_pr=para_pr,
                        style_id=style_id,
                        char_pr=char_pr,
                        depth=ladder.get((para_pr, style_id, char_pr), 1),
                    )
                )
    return found


def _depth_ladder(
    paragraphs: list[str], indents: dict[str, int]
) -> dict[tuple[str, str, str], int]:
    """Rank the section's paragraph styles by left indent → outline depth (1-based).

    Only used to label matches in :func:`find_anchors`; insertion picks its templates
    from the anchor's own neighbourhood instead (see :func:`_templates_by_indent`),
    because a whole section's style list is far longer than any local outline.
    """
    seen: dict[tuple[str, str, str], int] = {}
    for p in paragraphs:
        key = _ids(p)
        if key == ("", "", "") or key in seen:
            continue
        seen[key] = indents.get(key[0], 0)
    order = sorted({v for v in seen.values()})
    return {k: order.index(v) + 1 for k, v in seen.items()}


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clone(template: str, text: str) -> str:
    """Copy *template*'s paragraph shell and runs, carrying only *text*.

    The line-layout cache is dropped: a body paragraph does not need one and a stale
    one would describe the template's text, not this one.
    """
    runs = _RUN_RE.findall(template)
    if not runs:
        raise TextEditError("복제할 문단에 <hp:run> 이 없음")
    shell = _OPEN_RE.search(template)
    if shell is None:  # pragma: no cover - _P_RE guarantees an open tag
        raise TextEditError("문단 여는 태그를 찾지 못함")
    run = _LINESEG_RE.sub("", runs[0])
    run = _T_RE.sub(lambda _m: f"<hp:t>{_esc(text)}</hp:t>", run, count=1)
    # a template run may hold several <hp:t>; keep only the one we just wrote
    kept = False

    def _drop(_m: re.Match[str]) -> str:
        nonlocal kept
        if not kept:
            kept = True
            return _m.group(0)
        return ""

    run = _T_RE.sub(_drop, run)
    para_id = random.randint(10**8, 2 * 10**9)
    shell_xml = re.sub(r'\bid="\d+"', f'id="{para_id}"', shell.group(0), count=1)
    return f"{shell_xml}{run}</hp:p>"


def _templates_by_indent(
    paragraphs: list[str], indents: dict[str, int], anchor_index: int
) -> dict[int, str]:
    """Nearest paragraph to the anchor for each distinct left indent around it.

    Locality matters: the outline a reader sees on the page is the handful of indents
    used near the anchor, not every style the section happens to contain.
    """
    best: dict[int, tuple[int, str]] = {}
    lo = max(0, anchor_index - _NEIGHBOURHOOD)
    hi = min(len(paragraphs), anchor_index + _NEIGHBOURHOOD + 1)
    for i in range(lo, hi):
        p = paragraphs[i]
        para_pr = _ids(p)[0]
        if not para_pr or not _para_text(p).strip():
            continue
        indent = indents.get(para_pr, 0)
        distance = abs(i - anchor_index)
        if indent not in best or distance < best[indent][0]:
            best[indent] = (distance, p)
    return {ind: p for ind, (_, p) in best.items()}


def insert_markdown(
    path: str | Path,
    markdown: str,
    *,
    anchor: str,
    where: str = "after",
    output: str | Path | None = None,
    occurrence: int | None = None,
    anchor_level: int = 1,
) -> InsertResult:
    """Insert *markdown*'s paragraphs next to the paragraph matching *anchor*.

    *occurrence* (1-based) picks one when the anchor is not unique; without it an
    ambiguous anchor is an error, because guessing would edit the wrong place.

    *anchor_level* says which Markdown bullet level the anchor paragraph itself sits
    at, which is how the Markdown outline is pinned onto the document's own indents.
    The default 1 means "a top-level bullet lands where the anchor is". Anchor a
    second-level bullet and pass ``anchor_level=2`` so a top-level bullet steps one
    indent out instead of landing on top of it.
    """
    if where not in ("before", "after"):
        raise TextEditError("where 는 before 또는 after")

    src = Path(path)
    dst = Path(output) if output else src.with_suffix(".edited.hwpx")

    blocks = parse_markdown(markdown)
    bad = [b for b in blocks if isinstance(b, TableBlock) or b.kind in ("heading", "rule")]
    if bad:
        raise TextEditError(
            "제목·표는 번호 체계를 건드리므로 이 명령의 범위 밖임 — write 로 재조판할 것"
        )
    blocks = [b for b in blocks if isinstance(b, Block) and b.text.strip()]
    if not blocks:
        raise TextEditError("삽입할 내용이 없음")

    candidates = find_anchors(src, anchor)
    if not candidates:
        raise TextEditError(f"앵커를 찾지 못함: {anchor!r}")
    if len(candidates) > 1 and occurrence is None:
        preview = "; ".join(f"[{i + 1}] {c.text[:40]}" for i, c in enumerate(candidates[:5]))
        raise TextEditError(
            f"앵커가 {len(candidates)}곳과 일치함 — 더 길게 적거나 "
            f"--occurrence 로 고를 것: {preview}"
        )
    target = candidates[(occurrence - 1) if occurrence else 0]
    if occurrence is not None and not 1 <= occurrence <= len(candidates):
        raise TextEditError(f"--occurrence 는 1..{len(candidates)} 범위")

    section_xml = _sections(src)[target.section]
    spans = [(m.start(), m.end()) for m in _P_RE.finditer(section_xml)]
    paragraphs = _P_RE.findall(section_xml)
    indents = _indent_by_para_pr(_header(src))
    templates = _templates_by_indent(paragraphs, indents, target.index)
    if not templates:
        raise TextEditError("앵커 주변에서 본뜰 문단을 찾지 못함")

    if anchor_level < 1:
        raise TextEditError("anchor_level 은 1 이상")
    anchor_indent = indents.get(target.para_pr, 0)
    # Pin the Markdown outline onto the document's own indents: the anchor is known to
    # sit at *anchor_level*, so level N is that many steps away on the local ladder.
    ladder = sorted(templates)
    try:
        base = ladder.index(anchor_indent) - (anchor_level - 1)
    except ValueError:  # pragma: no cover - the anchor is always among the templates
        base = 0

    result = InsertResult(output=dst, anchor=target, where=where)
    chunks: list[str] = []
    for block in blocks:
        if block.kind in ("bullet", "ordered") and block.level >= 1:
            want_at = base + block.level - 1
            clamped = min(max(want_at, 0), len(ladder) - 1)
            if clamped != want_at:
                result.warnings.append(
                    f"{block.level}단계 글머리에 해당하는 문단이 앵커 주변에 없어 "
                    f"{clamped + 1 - base}단계 서식을 씀"
                )
            want = ladder[clamped]
        else:
            want = anchor_indent
        template = templates.get(want) or templates[min(templates, key=lambda i: abs(i - want))]
        chunks.append(_clone(template, block.text))

    at = spans[target.index][1] if where == "after" else spans[target.index][0]
    edited = section_xml[:at] + "".join(chunks) + section_xml[at:]
    _rewrite_zip_preserving(src, dst, {target.section: edited.encode("utf-8")})
    result.inserted = len(chunks)
    return result
