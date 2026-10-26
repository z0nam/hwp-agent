"""Cover / colophon metadata: bake slots into a house form, then fill them (issue #32).

Two halves of roadmap Phase 1 (``docs/full-report-roadmap.md`` 실행 상세 B):

* **B1 — bake** (:func:`bake_meta_slots`): a house form ships a worked example on
  its 표지 (cover) and 판권 (colophon) — "제주 경제전망 2026", "강영준 제주연구원
  연구위원", "978-89-6010-000-0 00000" … Those strings differ per form, so each
  form's ``overrides/<form>.json`` declares a ``meta_slots`` map (example string →
  ``{{slot}}``) and ``scripts/refresh-house-forms.py`` swaps them in while baking.
  Only the matched substring changes; the label run (``연구책임``) keeps its own
  character style.
* **B2 — fill** (:func:`apply_report_meta`): ``meta.json`` / Markdown front matter
  → the existing :func:`~hwp_agent.ops.form.fill_form`, run by ``build`` (and
  ``write``) after the body is placed. It also sets the document properties in
  ``Contents/content.hpf`` (``opf:title`` / ``creator``, #51 ④).

A slot may carry a default after a colon — ``{{report_series:전략연구}}`` — used when
the metadata has no value for it (the series name is form-specific, the number
is not).
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from hwpx.document import HwpxDocument

from .template import RESIDUAL_TOKEN_RE

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


#: Slot names this module fills. Anything else in ``{{…}}`` is left to other steps.
META_SLOTS = (
    "report_series",
    "report_number",
    "title",
    "subtitle",
    "authors",
    "lead_researcher",
    "co_researcher",
    "pub_date",
    "publisher",
    "printer",
    "isbn",
)
#: Slots a submission copy must not leave blank — missing ones are blanked *and*
#: reported. The rest (subtitle, co_researcher, printer, report_series with its
#: form default) are legitimately empty on many reports, so they blank silently.
REQUIRED_SLOTS = (
    "report_number",
    "title",
    "authors",
    "lead_researcher",
    "pub_date",
    "publisher",
    "isbn",
)

#: Korean keys accepted in meta.json / front matter, mapped to slot names.
ALIASES = {
    "제목": "title",
    "부제": "subtitle",
    "부제목": "subtitle",
    "총서": "report_series",
    "총서명": "report_series",
    "시리즈": "report_series",
    "과제번호": "report_number",
    "보고서번호": "report_number",
    "발간번호": "report_number",
    "저자": "authors",
    "표지저자": "authors",
    "연구책임": "lead_researcher",
    "공동책임": "co_lead",
    "공동연구": "co_researchers",
    "발행일": "pub_date",
    "발행인": "publisher",
    "인쇄처": "printer",
    "종료일": "end_date",
    "과제종료일": "end_date",
    "작성자": "creator",
    "author": "creator",
}

#: 판권 「발행인」 = the 원장 on the project's end date (연구기획전략실, 2026-10-21).
#: Source: ``~/dev/agent-rules/docs/ji-facts.md`` (원장 — 2026-10-06 부로 직무대행 체제).
#: ``(first day, last day or None, name as the forms print it — letter-spaced)``.
#: Re-check that note when a new 원장 takes office; an explicit ``publisher`` always wins.
PUBLISHERS = (
    (date(2025, 5, 2), date(2026, 10, 5), "유 영 봉"),
    (date(2026, 10, 6), None, "강 창 민"),
)


@dataclass
class MetaResult:
    filled: list[str] = field(default_factory=list)
    blank: list[str] = field(default_factory=list)  # slots emptied for lack of a value
    missing: list[str] = field(default_factory=list)  # required slots among ``blank``
    warnings: list[str] = field(default_factory=list)
    title: str | None = None  # what went into opf:title
    creator: str | None = None  # what went into the creator meta

    def as_dict(self) -> dict:
        return {
            "filled": self.filled,
            "blank": self.blank,
            "missing": self.missing,
            "warnings": self.warnings,
            "title": self.title,
            "creator": self.creator,
        }


# --------------------------------------------------------------------------- #
# Paragraph text helpers (direct runs only — a nested table/textbox paragraph
# is visited on its own)
# --------------------------------------------------------------------------- #
def _direct_ts(p) -> list:
    return [t for r in p.findall(_hp("run")) for t in r.findall(_hp("t"))]


def _para_text(p) -> str:
    """Text of the plain ``<hp:t>`` (no tab/line-break children) — what we can edit."""
    return "".join(t.text or "" for t in _direct_ts(p) if len(t) == 0)


def _full_text(p) -> str:
    """All visible text of *p*'s direct runs (child tails included) — for scanning."""
    return "".join(
        (t.text or "") + "".join(c.tail or "" for c in t) for t in _direct_ts(p)
    )


def _replace_in_para(p, old: str, new: str) -> bool:
    """Replace *old* with *new* in *p*'s visible text, even across runs.

    The replacement lands in the ``<hp:t>`` where *old* starts (so it takes that
    run's character style); the rest of the matched text is cut from the
    following ``<hp:t>``s. ``<hp:t>`` with children (tab, line break) are skipped
    — example strings never straddle them.
    """
    ts = [t for t in _direct_ts(p) if len(t) == 0]
    text = "".join(t.text or "" for t in ts)
    start = text.find(old)
    if start < 0:
        return False
    end = start + len(old)
    pos = 0
    placed = False
    for t in ts:
        s = t.text or ""
        lo, hi = pos, pos + len(s)
        pos = hi
        if hi <= start or lo >= end:
            continue
        a, b = max(start, lo) - lo, min(end, hi) - lo
        if not placed:
            t.text = s[:a] + new + s[b:]
            placed = True
        else:
            t.text = s[:a] + s[b:]
    return True


# --------------------------------------------------------------------------- #
# B1 — bake slots into a house form
# --------------------------------------------------------------------------- #
def _section_index(n_sections: int, spec) -> int:
    idx = int(spec)
    return idx + n_sections if idx < 0 else idx


def bake_meta_slots(path: Path | str, config: dict) -> list[str]:
    """Swap a form's cover/colophon example strings for ``{{slots}}``, in place.

    *config* is the ``meta_slots`` block of ``overrides/<form>.json``::

        {"cover":    {"section": 0,  "replace": {"제주 경제전망 2026": "{{title}}", …}},
         "colophon": {"section": -1, "replace": {…}, "append": {"인 쇄 처": "{{printer}}"}}}

    ``replace`` keys are example substrings; each must occur in exactly one
    paragraph of that section, or :class:`ValueError` is raised — a revised form
    whose example changed should fail the bake loudly, not ship half-slotted.
    ``append`` adds the token at the end of the paragraph starting with the key
    (for a label whose example value is empty). An entry whose token is already in
    the section is skipped, so re-baking a baked copy is a no-op.

    Returns ``["s0:{{title}}", …]`` for the bake summary.
    """
    doc = HwpxDocument.open(str(path))
    n = len(doc.sections)
    done: list[str] = []
    errors: list[str] = []
    for part in ("cover", "colophon"):
        spec = config.get(part)
        if not spec:
            continue
        si = _section_index(n, spec.get("section", 0 if part == "cover" else -1))
        sec = doc.sections[si]
        paras = list(sec.element.iter(_hp("p")))
        sec_text = "\n".join(_para_text(p) for p in paras)
        for old, new in (spec.get("replace") or {}).items():
            if new in sec_text:
                continue
            hits = [p for p in paras if old in _para_text(p)]
            if len(hits) != 1:
                errors.append(f"s{si}: {old!r} found in {len(hits)} paragraph(s), want 1")
                continue
            _replace_in_para(hits[0], old, new)
            done.append(f"s{si}:{new}")
        for label, token in (spec.get("append") or {}).items():
            if token in sec_text:
                continue
            hits = [p for p in paras if _para_text(p).strip().startswith(label)]
            if len(hits) != 1:
                errors.append(f"s{si}: label {label!r} found {len(hits)} time(s), want 1")
                continue
            ts = [t for t in _direct_ts(hits[0]) if len(t) == 0]
            if not ts:
                errors.append(f"s{si}: label {label!r} has no text run to extend")
                continue
            ts[-1].text = (ts[-1].text or "") + token
            done.append(f"s{si}:{token}")
        sec.mark_dirty()
    if errors:
        raise ValueError("meta_slots: " + "; ".join(errors))
    if done:
        doc.save_to_path(str(path))
    return done


# --------------------------------------------------------------------------- #
# Metadata sources
# --------------------------------------------------------------------------- #
_FRONT_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            return json.loads(raw)  # "…\n…" escapes
        except ValueError:
            return raw[1:-1]
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    return raw


def split_front_matter(markdown: str) -> tuple[dict, str]:
    """Split a leading ``---`` front-matter block off *markdown*.

    Returns ``(meta, body)``; ``meta`` is ``{}`` (and *body* unchanged) when there
    is none. Only the flat subset the cover needs is read — no YAML dependency::

        ---
        title: "제주 관광 수요 전망을 위한\\n기초 연구"
        report_number: 2025-06
        lead_researcher: 홍길동  제주연구원 연구위원
        co_researchers:
          - 김갑을  제주연구원 연구위원
        ---

    ``key: value`` scalars (quoted or bare; ``"…"`` takes ``\\n`` escapes), ``key:``
    followed by ``- item`` lines (a list), and ``key: |`` followed by indented lines
    (joined with newlines). ``#`` lines are comments.
    """
    m = _FRONT_RE.match(markdown)
    if not m:
        return {}, markdown
    meta: dict = {}
    key: str | None = None
    mode: str | None = None  # "list" | "block" after a bare "key:" / "key: |"
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if key and line[:1] in (" ", "\t"):
            item = line.strip()
            if mode == "block":
                meta[key] = (meta[key] + "\n" if meta[key] else "") + item
                continue
            if item.startswith("- "):
                if not isinstance(meta.get(key), list):
                    meta[key] = []
                meta[key].append(_scalar(item[2:]))
                mode = "list"
                continue
        k, sep, v = line.partition(":")
        if not sep:
            continue
        key = k.strip()
        v = v.strip()
        if v in ("|", "|-"):
            meta[key], mode = "", "block"
        elif v:
            meta[key], mode = _scalar(v), None
        else:
            meta[key], mode = "", "list"
    return meta, markdown[m.end():]


def meta_keys(meta: dict) -> dict:
    """Only the entries of *meta* this module understands.

    Front matter is often project bookkeeping (``문서명:``, ``작성일:``); such a
    block must not count as "metadata supplied" and blank the cover slots.
    """
    known = {*META_SLOTS, *ALIASES, "co_lead", "co_researchers", "end_date", "creator"}
    return {k: v for k, v in meta.items() if str(k).strip() in known}


def load_meta(source) -> dict:
    """A meta dict from a ``meta.json`` path or an already-parsed dict."""
    if source is None:
        return {}
    if isinstance(source, dict):
        return dict(source)
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{source}: meta must be a JSON object")
    return data


def first_heading(markdown: str) -> str | None:
    """Text of the first ATX heading in *markdown* (for the opf:title fallback)."""
    for line in markdown.splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1).strip()
    return None


def _as_list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [s.strip() for s in str(value).split("\n") if s.strip()]


def _name_of(researcher: str) -> str:
    """``"홍길동  제주연구원 연구위원"`` → ``"홍길동"`` (the cover shows names only)."""
    return researcher.split()[0] if researcher.split() else ""


def _format_pub_date(value: str) -> str:
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})(?:[-./]\d{1,2})?", value.strip())
    return f"{m.group(1)}년 {int(m.group(2))}월" if m else value


def publisher_for(end_date: str | date | None) -> str | None:
    """판권 발행인 for a project ending on *end_date* (see :data:`PUBLISHERS`)."""
    if not end_date:
        return None
    if isinstance(end_date, str):
        try:
            end_date = date.fromisoformat(end_date.strip())
        except ValueError:
            return None
    for first, last, name in PUBLISHERS:
        if end_date >= first and (last is None or end_date <= last):
            return name
    return None


def normalize_meta(meta: dict) -> dict:
    """Map aliases, derive the cover/colophon values, drop empties.

    Derived: ``authors`` (cover: 연구책임 + 공동책임 names, ``·``-joined — the 2026
    표지 shows no 공동연구), ``co_researcher`` (list, one colophon line each),
    ``publisher`` (from ``end_date``), ``pub_date`` (``2026-08`` → ``2026년 8월``),
    ``subtitle`` (``: `` prefix the forms use).
    """
    m: dict = {}
    for k, v in meta.items():
        k = ALIASES.get(str(k).strip(), str(k).strip())
        if isinstance(v, (int, float)):
            v = str(v)
        if v in (None, "", []):
            continue
        m[k] = v
    lead = str(m.get("lead_researcher", "")).strip()
    co_lead = _as_list(m.get("co_lead"))
    co = _as_list(m.get("co_researchers", m.get("co_researcher")))
    out: dict = {
        k: str(v).strip()
        for k, v in m.items()
        if k in META_SLOTS and k != "co_researcher" and str(v).strip()
    }
    if co_lead or co:
        # 공동책임 are 공동연구 on the colophon too, listed first
        out["co_researcher"] = co_lead + [c for c in co if c not in co_lead]
    if "authors" not in out and lead:
        out["authors"] = "·".join(_name_of(r) for r in [lead, *co_lead])
    if "publisher" not in out and (pub := publisher_for(m.get("end_date"))):
        out["publisher"] = pub
    if "pub_date" in out:
        out["pub_date"] = _format_pub_date(out["pub_date"])
    sub = out.get("subtitle")
    if sub and not sub.startswith((":", "-", "—", "–")):
        out["subtitle"] = f": {sub}"
    if m.get("creator"):
        out["creator"] = str(m["creator"]).strip()
    return out


def profile_name() -> str | None:
    """Only the ``name`` field of ``~/.config/hwp-agent/profile.json`` — never more.

    The profile also holds addresses and an account; none of that is cover
    metadata, so nothing else is read out of it here.
    """
    from .profile import resolve_profile_path

    p = resolve_profile_path(None)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


# --------------------------------------------------------------------------- #
# B2 — fill
# --------------------------------------------------------------------------- #
def _slot_parts(token_name: str) -> tuple[str, str | None]:
    base, sep, default = token_name.partition(":")
    return base.strip(), (default.strip() if sep else None)


def _meta_tokens(doc: HwpxDocument) -> list[str]:
    """Distinct ``{{…}}`` names in *doc* that are meta slots (with any default)."""
    seen: list[str] = []
    for sec in doc.sections:
        for p in sec.element.iter(_hp("p")):
            for name in RESIDUAL_TOKEN_RE.findall(_full_text(p)):
                name = name.strip()
                if _slot_parts(name)[0] in META_SLOTS and name not in seen:
                    seen.append(name)
    return seen


def _prefix_to_blanks(p, token: str) -> None:
    """Replace the label before *token* with same-width ideographic spaces.

    Continuation lines of 공동연구 align under the first one: each label character
    becomes one U+3000 (full-width, like a Hangul syllable), each space stays a
    space. The blanks move into the *value* run (the token's ``<hp:t>``) — some
    label fonts draw U+3000 as faint dots (KoPub돋움 Medium does, in Hangul's PDF).
    """
    ts = _direct_ts(p)
    head = ""
    for t in ts:
        s = t.text or ""
        cut = s.find(token)
        if cut < 0:
            head += s
            t.text = ""
            continue
        head += s[:cut]
        t.text = re.sub(r"\S", "\u3000", head) + s[cut:]
        break


_SPACER_TAGS = {_hp("p"), _hp("run"), _hp("t"), _hp("linesegarray"), _hp("lineseg")}


def _is_spacer(p) -> bool:
    """An empty paragraph used only for vertical space (no text, no object)."""
    return p.tag == _hp("p") and all(
        el.tag in _SPACER_TAGS and not (el.tag == _hp("t") and (el.text or "").strip())
        for el in p.iter()
    )


def _expand_co_researchers(doc: HwpxDocument, count: int) -> list[str]:
    """Give ``{{co_researcher}}`` one colophon line per 공동연구 (or drop the line).

    Returns the token names to fill, in order (``co_researcher``,
    ``co_researcher__2``, …). With *count* 0 the whole ``공동연구 …`` paragraph is
    removed — a label with nothing after it reads as a form left unfinished.

    The 판권 is one page laid out with empty spacer paragraphs; each added line
    takes out the nearest spacer above the 연구진 block so the page keeps its
    height (otherwise the 면책 문구 spills onto a new page in Hangul).
    """
    token = "{{co_researcher}}"
    names: list[str] = []
    for sec in doc.sections:
        for p in list(sec.element.iter(_hp("p"))):
            if token not in _full_text(p):
                continue
            parent = p.getparent()
            if count == 0:
                if parent is not None:
                    parent.remove(p)
                    sec.mark_dirty()
                continue
            if "co_researcher" not in names:
                names.append("co_researcher")
            spacers = [q for q in p.itersiblings(preceding=True) if _is_spacer(q)]
            for q in spacers[: count - 1]:
                q.getparent().remove(q)
            anchor = p
            for i in range(2, count + 1):
                clone = copy.deepcopy(p)
                for lsa in clone.findall(_hp("linesegarray")):
                    clone.remove(lsa)
                _prefix_to_blanks(clone, token)
                _replace_in_para(clone, token, f"{{{{co_researcher__{i}}}}}")
                anchor.addnext(clone)
                anchor = clone
                if f"co_researcher__{i}" not in names:
                    names.append(f"co_researcher__{i}")
            sec.mark_dirty()
    return names


def _drop_slot_paragraphs(doc: HwpxDocument, slot: str) -> int:
    """Remove paragraphs holding nothing but ``{{slot}}`` (an optional slot left
    empty — e.g. no 부제: its blank line would push the 판권 past one page)."""
    token = "{{" + slot + "}}"
    n = 0
    for sec in doc.sections:
        for p in list(sec.element.iter(_hp("p"))):
            if _full_text(p).strip() == token and p.find(".//" + _hp("secPr")) is None:
                p.getparent().remove(p)
                sec.mark_dirty()
                n += 1
    return n


def _newlines_to_breaks(doc: HwpxDocument) -> None:
    """``\\n`` inside filled text → ``<hp:lineBreak/>`` (two-line cover titles)."""
    for sec in doc.sections:
        touched = False
        for t in list(sec.element.iter(_hp("t"))):
            pieces: list[tuple[object, str]] = [(None, t.text or "")]
            pieces += [(c, c.tail or "") for c in t]
            if not any("\n" in s for _, s in pieces):
                continue
            touched = True
            new_t = t.makeelement(t.tag, dict(t.attrib))
            cur = None  # last child appended to new_t
            for child, s in pieces:
                if child is not None:
                    child.tail = None
                    new_t.append(child)
                    cur = child
                for j, seg in enumerate(s.split("\n")):
                    if j:
                        br = new_t.makeelement(_hp("lineBreak"), {})
                        new_t.append(br)
                        cur = br
                    if cur is None:
                        new_t.text = (new_t.text or "") + seg
                    else:
                        cur.tail = (cur.tail or "") + seg
            t.getparent().replace(t, new_t)
        if touched:
            sec.mark_dirty()


def apply_report_meta(
    path: Path | str,
    meta: dict | None,
    *,
    fallback_title: str | None = None,
    author: str | None = None,
    use_profile: bool = True,
) -> MetaResult:
    """Fill the cover/colophon slots of *path* from *meta* and set its properties.

    Missing-value policy (#32): with *meta* given (any source), every meta slot is
    consumed — a slot without a value is **blanked**, never left as ``{{…}}``, and
    the required ones (:data:`REQUIRED_SLOTS`) are listed in ``missing`` plus a
    warning so the gap is visible before submission. With *meta* ``None`` (no
    meta.json, no front matter) the slots are left for a later ``form fill`` and
    one warning names them.

    Document properties (``content.hpf``) are always set:
    ``opf:title`` ← meta title (line breaks → spaces), else *fallback_title*
    (the Markdown's first heading); ``creator`` ← meta ``creator``, else the
    연구책임 name, else *author* (``--author``), else the profile ``name``.
    """
    from .form import fill_form
    from .metadata import update_metadata

    res = MetaResult()
    norm = normalize_meta(meta) if meta is not None else {}

    doc = HwpxDocument.open(str(path))
    tokens = _meta_tokens(doc)
    if meta is None:
        if tokens:
            toks = ", ".join("{{" + t + "}}" for t in tokens)
            res.warnings.append(
                f"표지·판권 슬롯 {len(tokens)}개를 채우지 않았습니다 ({toks}) — "
                "meta.json(--meta) 또는 MD 프런트매터로 채우거나 이후 form fill 로 채우세요"
            )
    elif tokens:
        co = norm.get("co_researcher") or []
        has_co = any(_slot_parts(t)[0] == "co_researcher" for t in tokens)
        co_names = _expand_co_researchers(doc, len(co)) if has_co else []
        if has_co:
            (res.filled if co else res.blank).append("co_researcher")
        if not norm.get("subtitle") and _drop_slot_paragraphs(doc, "subtitle"):
            res.blank.append("subtitle")
        doc.save_to_path(str(path))
        tokens = _meta_tokens(doc)  # re-read: lines were added / dropped

        mapping: dict[str, str] = dict(zip(co_names, co, strict=True)) if co_names else {}
        for name in tokens:
            base, default = _slot_parts(name)
            if base == "co_researcher":
                continue
            value = norm.get(base) or default or ""
            mapping[name] = value
            bucket = res.filled if value else res.blank
            if base not in bucket:
                bucket.append(base)
        if mapping:
            fr = fill_form(path, mapping)
            res.warnings += fr.warnings
            # a token fill_form could not place (should not happen: we just found it)
            for key in fr.missing:
                res.warnings.append(f"slot {{{{{key}}}}} could not be filled")
        res.missing = [s for s in REQUIRED_SLOTS if s in res.blank]
        if res.missing:
            res.warnings.append(
                "표지·판권 값이 없어 비워 둔 필수 항목: "
                + ", ".join(res.missing)
                + " — meta.json/프런트매터에 넣어 다시 build 하세요"
            )
        doc = HwpxDocument.open(str(path))
        _newlines_to_breaks(doc)
        doc.save_to_path(str(path))

    title = (norm.get("title") or "").replace("\n", " ").strip() or (
        fallback_title.strip() if fallback_title else None
    )
    lead = norm.get("lead_researcher")
    creator = (
        norm.get("creator")
        or (_name_of(lead) if lead else None)
        or (author.strip() if author else None)
        or (profile_name() if use_profile else None)
    )
    props = {k: v for k, v in (("title", title), ("creator", creator)) if v}
    if props:
        update_metadata(path, **props)
    res.title, res.creator = title, creator
    return res


_UNCONSUMED = "unconsumed template marker(s) left in document"


def finish_report_meta(
    result,
    path: Path | str,
    meta: dict | None,
    *,
    fallback_title: str | None = None,
    author: str | None = None,
) -> MetaResult:
    """Run :func:`apply_report_meta` after write/build and fold it into *result*.

    The body step reported every ``{{…}}`` it saw — the cover slots included — as
    unconsumed; that warning is replaced by one computed after the fill, so it
    names only what is really left.
    """
    mres = apply_report_meta(path, meta, fallback_title=fallback_title, author=author)
    result.warnings = [w for w in result.warnings if _UNCONSUMED not in w]
    result.warnings += mres.warnings
    meta_names = set(_meta_tokens(HwpxDocument.open(str(path))))
    left = [t for t in residual_tokens(path) if t.strip("{} ") not in meta_names]
    if left:
        result.warnings.append(f"{_UNCONSUMED}: {', '.join(sorted(left))}")
    result.meta = mres.as_dict()
    return mres


def residual_tokens(path: Path | str) -> list[str]:
    """Every ``{{…}}`` still in *path* (paragraph text, incl. tables/captions)."""
    doc = HwpxDocument.open(str(path))
    out: list[str] = []
    for sec in doc.sections:
        for p in sec.element.iter(_hp("p")):
            for name in RESIDUAL_TOKEN_RE.findall(_full_text(p)):
                tok = "{{" + name.strip() + "}}"
                if tok not in out:
                    out.append(tok)
    return out
