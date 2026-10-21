"""Diagnose a type-1 template: surface unclear / incomplete style-system parts.

``hwp-agent author`` is only as good as the template's style system. This checks
the system a structured template exposes and reports what is ambiguous or missing:

* **ladder gaps** — a missing `HEADING_n` / `BULLET_n` / `ORDERED_n` level;
* **hierarchy violations** — a deeper level whose font is *larger* than a
  shallower one (`docs/template-convention.md` font-size principle);
* **un-mapped ladder siblings** — several styles at the *same* outline level
  (e.g. two different bullets), where the role map can pick only one, so authoring
  can't target the rest;
* **un-mapped structural styles** — named, used styles outside the role system
  (note, caption, abstract, references, TOC … the parts of a *complete* report).

Read-only. Returns a structured report; the CLI renders it (text or ``--json``).
"""

from __future__ import annotations

from hwpx.document import HwpxDocument

from .integrity import audit_package
from .linesegs import iter_mismatches
from .styles import classify_document, read_style_system, role_map

_HH = "{http://www.hancom.co.kr/hwpml/2011/head}"


def _size_lookup(doc: HwpxDocument):
    """Return a ``style_id -> font size in pt`` resolver (HWP height is 1/100 pt)."""
    try:
        cps = doc._root._headers[0]._char_properties_element(create=False)
    except Exception:
        cps = None

    def size(style_id: str) -> float | None:
        if cps is None:
            return None
        try:
            char = doc.style(style_id).char_pr_id_ref
        except Exception:
            return None
        if char is None:
            return None
        el = cps.find(f"{_HH}charPr[@id='{char}']")
        h = el.get("height") if el is not None else None
        return round(int(h) / 100, 1) if h else None

    return size


def _ladder(
    prefix: str, roles: dict, infos: dict, size, *, strict: bool = True
) -> tuple[list[dict], list[str], list[str]]:
    """Build a role ladder (HEADING_/BULLET_/ORDERED_) with gap + size-order checks.

    ``strict=False`` (used for BULLET) builds the rungs but skips gap and
    size-hierarchy checks: HWP encodes bullet nesting by the bullet *glyph*, not
    the outline level (``■`` is the parent, ``-`` nests under it), so an
    outline-level-derived BULLET_n order can't be judged for gaps or size
    monotonicity — see backlog item G. The ladder order must instead come from an
    explicit ``AI:BULLET_n`` declaration.
    """
    levels = sorted(int(r.split("_")[1]) for r in roles if r.startswith(prefix))
    rungs, gaps, violations = [], [], []
    if not levels:
        return rungs, gaps, violations
    prev_size = None
    for n in range(min(levels), max(levels) + 1):
        role = f"{prefix}{n}"
        sid = roles.get(role)
        if sid is None:
            if strict:
                gaps.append(role)
            continue
        sz = size(sid)
        rungs.append(
            {"level": n, "role": role, "style": sid,
             "name": infos[sid].name if sid in infos else "", "size": sz}
        )
        if strict and prev_size is not None and sz is not None and sz > prev_size:
            violations.append(f"{role} ({sz}pt) > shallower level ({prev_size}pt)")
        if sz is not None:
            prev_size = sz
    return rungs, gaps, violations


def _marker_audit(doc, inst_style: str | None = None) -> list[dict]:
    """Locate each insertion marker, ``{{table_template}}``, and ``{{figure_template}}``.

    ``write``/``build`` route content to these markers and copy table styling from a
    ``{{table…}}``-captioned reference table; a marker that appears twice is ambiguous
    (which one gets the content?) and one that appears in an unexpected place routes
    content to the wrong section. Reporting the placement up front makes those mistakes
    visible without a render (the deterministic half of a house-form assembly check).
    Returns ``[{token, section, count}, …]`` in document order.

    ``AI:INSTRUCTION`` paragraphs are skipped, mirroring ``fill_from_markdown``, which
    removes them before selecting a marker — otherwise an instruction that merely
    *mentions* a token (e.g. "content is written at ``{{body}}``") is miscounted as a
    second live marker and falsely flagged ambiguous.
    """
    from .author import _HP, _caption_text
    from .template import (
        FIGURE_TEMPLATE_TOKEN,
        FIGURE_TOKEN_RE,
        INSERTION_MARKERS,
        RESIDUAL_TOKEN_RE,
        TABLE_TEMPLATE_TOKEN,
        TABLE_TOKEN_RE,
    )

    table_token = TABLE_TEMPLATE_TOKEN
    figure_token = FIGURE_TEMPLATE_TOKEN
    order = (*INSERTION_MARKERS, table_token, figure_token)
    out: list[dict] = []
    for si, section in enumerate(doc.sections):
        counts: dict[str, int] = {}
        for p in section.paragraphs:
            if inst_style is not None and str(p.style_id_ref) == inst_style:
                continue  # instruction paragraph — removed before the writer picks a marker
            text = "".join(p.element.itertext())
            for m in INSERTION_MARKERS:
                if m in text:
                    counts[m] = counts.get(m, 0) + 1
            if FIGURE_TOKEN_RE.search(text):
                counts[figure_token] = counts.get(figure_token, 0) + 1
            for res in RESIDUAL_TOKEN_RE.findall(text):
                if (
                    res not in order
                    and not TABLE_TOKEN_RE.match(res)
                    and not FIGURE_TOKEN_RE.match(res)
                ):
                    counts[res] = counts.get(res, 0) + 1
        for tbl in section.element.iter(f"{{{_HP}}}tbl"):
            cap_txt = _caption_text(tbl)
            if TABLE_TOKEN_RE.search(cap_txt):
                counts[table_token] = counts.get(table_token, 0) + 1
            for res in RESIDUAL_TOKEN_RE.findall(cap_txt):
                if (
                    res not in order
                    and not TABLE_TOKEN_RE.match(res)
                    and not FIGURE_TOKEN_RE.match(res)
                ):
                    counts[res] = counts.get(res, 0) + 1
        for m in order:  # stable order
            if m in counts:
                out.append({"token": m, "section": si, "count": counts.pop(m)})
        for res, cnt in sorted(counts.items()):
            out.append({"token": res, "section": si, "count": cnt})
    return out


def diagnose_template(path: str) -> dict:
    """Diagnose the style system of a (type-1) template; see module docstring."""
    doc = HwpxDocument.open(path)
    size = _size_lookup(doc)
    roles = role_map(path)
    infos = {i.style_id: i for i in read_style_system(path)}
    roled_ids = set(roles.values())

    ladders, gaps, violations = {}, [], []
    for prefix in ("HEADING_", "BULLET_", "ORDERED_"):
        # bullets: nesting is by glyph, not outline level — don't gap/size-check
        rungs, g, v = _ladder(prefix, roles, infos, size, strict=prefix != "BULLET_")
        ladders[prefix.rstrip("_")] = rungs
        gaps += g
        violations += v

    # styles that are used but not in the role system, split into ladder siblings
    # (a bullet/outline style competing at a level) and other structural styles
    siblings, structural = [], []
    for i in infos.values():
        if i.style_id in roled_ids or i.use_count == 0:
            continue
        entry = {
            "style": i.style_id, "name": i.name, "size": size(i.style_id),
            "heading": i.heading_type, "level": i.outline_level, "use": i.use_count,
        }
        (siblings if i.heading_type in ("BULLET", "OUTLINE") else structural).append(entry)
    siblings.sort(key=lambda e: -e["use"])
    structural.sort(key=lambda e: -e["use"])

    report = {
        "file": path,
        "classification": classify_document(path),
        "styles_total": len(infos),
        "roles_mapped": len(roles),
        "ladders": ladders,
        "body": (
            {"role": "BODY", "style": roles["BODY"],
             "name": infos[roles["BODY"]].name if roles.get("BODY") in infos else "",
             "size": size(roles["BODY"])}
            if "BODY" in roles else None
        ),
        "instruction": roles.get("INSTRUCTION"),
        "gaps": gaps,
        "hierarchy_violations": violations,
        "unmapped_ladder_siblings": siblings,
        "unmapped_structural": structural,
        "lineseg_mismatches": [m.as_dict() for m in iter_mismatches(path)],
        "markers": _marker_audit(doc, roles.get("INSTRUCTION")),
        "integrity": [i.as_dict() for i in audit_package(path)],
        "links": _link_summary(path),
        "equations": _equation_summary(doc),
    }
    report["warnings"] = _warnings(report)
    return report


def _equation_summary(doc: HwpxDocument) -> dict:
    """Equation objects vs LaTeX math left as text (issue #5) — informational.

    A ``$…$`` span counts as unconverted LaTeX only when it looks like math
    (a backslash command or ``^``/``_``), so prices like "$5 and $10" don't."""
    from .equation import math_pieces

    hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    count, leftover = 0, []
    for sec in doc.sections:
        count += len(sec.element.findall(f".//{{{hp}}}equation"))
        for p in sec.element.iter(f"{{{hp}}}p"):
            text = "".join(t.text or "" for t in p.findall(f"{{{hp}}}run/{{{hp}}}t"))
            if "$" not in text:
                continue
            for latex, kind in math_pieces(text):
                if kind and any(c in latex for c in "\\^_"):
                    leftover.append(latex.strip()[:60])
    return {"count": count, "latex_text": leftover}


def _link_summary(path) -> dict:
    """Hyperlink fields vs URL text left unlinked (issue #27) — informational."""
    from .links import audit_package_links

    a = audit_package_links(path)
    return {"hyperlinks": a["hyperlinks"], "bare_urls": a["bare_urls"]}


def _warnings(r: dict) -> list[str]:
    out = []
    if r["classification"] != "structured":
        out.append(f"document classifies as '{r['classification']}', not 'structured' — "
                   "author maps cleanly only on structured templates.")
    if r["gaps"]:
        out.append("ladder gaps (missing levels): " + ", ".join(r["gaps"]))
    for v in r["hierarchy_violations"]:
        out.append("font hierarchy: " + v)
    bullets = [s for s in r["unmapped_ladder_siblings"] if s["heading"] == "BULLET"]
    outlines = [s for s in r["unmapped_ladder_siblings"] if s["heading"] == "OUTLINE"]
    if bullets:
        names = ", ".join(f"'{b['name']}' ({b['use']}×)" for b in bullets[:3])
        out.append(
            f"{len(bullets)} bullet style(s) outside the role map ({names}) — bullet nesting is "
            "by glyph, not outline level, so the role map collapses siblings and drops these. "
            "Declare AI:BULLET_1/AI:BULLET_2/… on the bullet styles to set the ladder and make "
            "them targetable (docs/author-backlog.md item G), or run `hwp-agent normalize` "
            "to declare it automatically."
        )
    if outlines:
        top = outlines[0]
        out.append(
            f"{len(outlines)} used outline style(s) are unmapped — a heading level the role map "
            f"didn't pick (e.g. '{top['name']}' L{top['level']} used {top['use']}×); "
            "name it AI:H<n> to claim the level explicitly."
        )
    if not r["ladders"].get("BULLET"):
        out.append("no BULLET_n roles — a bullet-heavy report needs a complete bullet ladder.")
    lm = r.get("lineseg_mismatches") or []
    if lm:
        out.append(
            f"{len(lm)} paragraph(s) have a stale <hp:linesegarray> (lineBreak+1 > lineseg) — "
            "edited so lines were added but the layout cache is too small, so Hangul/rhwp pile "
            "the extra lines onto one. Run `hwp-agent normalize --drop-linesegarray` to clear the "
            "stale caches (Hangul recomputes them on open)."
        )
    # package integrity (issue #22): hand edits that shift/duplicate header ids or
    # drop a section's secPr break rendering silently — surface each one
    for e in r.get("integrity") or []:
        out.append(f"integrity [{e['kind']}] {e['where']}: {e['detail']}")
    # a marker appearing more than once is ambiguous — write/build can't tell which
    # paragraph should receive the content (PR #16 P1/P2 class of bug)
    from .template import STANDARD_TEMPLATE_MARKERS, TABLE_TOKEN_RE

    dup_total: dict[str, int] = {}
    for e in r.get("markers") or []:
        dup_total[e["token"]] = dup_total.get(e["token"], 0) + e["count"]
    for token, total in dup_total.items():
        if total > 1:
            out.append(
                f"marker {token} appears {total}× — ambiguous; write/build can't tell which "
                "paragraph gets the content. Keep exactly one per template."
            )
        if token not in STANDARD_TEMPLATE_MARKERS and not TABLE_TOKEN_RE.match(token):
            out.append(
                f"unrecognized marker {token} appears {total}× — ambiguous template token; "
                "write/build does not consume it."
            )
    return out
