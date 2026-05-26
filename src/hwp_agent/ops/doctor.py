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


def _ladder(prefix: str, roles: dict, infos: dict, size) -> tuple[list[dict], list[str], list[str]]:
    """Build a role ladder (HEADING_/BULLET_/ORDERED_) with gap + size-order checks."""
    levels = sorted(int(r.split("_")[1]) for r in roles if r.startswith(prefix))
    rungs, gaps, violations = [], [], []
    if not levels:
        return rungs, gaps, violations
    prev_size = None
    for n in range(min(levels), max(levels) + 1):
        role = f"{prefix}{n}"
        sid = roles.get(role)
        if sid is None:
            gaps.append(role)
            continue
        sz = size(sid)
        rungs.append(
            {"level": n, "role": role, "style": sid,
             "name": infos[sid].name if sid in infos else "", "size": sz}
        )
        if prev_size is not None and sz is not None and sz > prev_size:
            violations.append(f"{role} ({sz}pt) > shallower level ({prev_size}pt)")
        if sz is not None:
            prev_size = sz
    return rungs, gaps, violations


def diagnose_template(path: str) -> dict:
    """Diagnose the style system of a (type-1) template; see module docstring."""
    doc = HwpxDocument.open(path)
    size = _size_lookup(doc)
    roles = role_map(path)
    infos = {i.style_id: i for i in read_style_system(path)}
    roled_ids = set(roles.values())

    ladders, gaps, violations = {}, [], []
    for prefix in ("HEADING_", "BULLET_", "ORDERED_"):
        rungs, g, v = _ladder(prefix, roles, infos, size)
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
    }
    report["warnings"] = _warnings(report)
    return report


def _warnings(r: dict) -> list[str]:
    out = []
    if r["classification"] != "structured":
        out.append(f"document classifies as '{r['classification']}', not 'structured' — "
                   "author maps cleanly only on structured templates.")
    if r["gaps"]:
        out.append("ladder gaps (missing levels): " + ", ".join(r["gaps"]))
    for v in r["hierarchy_violations"]:
        out.append("font hierarchy: " + v)
    if r["unmapped_ladder_siblings"]:
        top = r["unmapped_ladder_siblings"][0]
        out.append(
            f"{len(r['unmapped_ladder_siblings'])} used bullet/outline style(s) are outside "
            f"the role map — authoring can't target them (e.g. '{top['name']}' "
            f"used {top['use']}×); the role map keeps one style per outline level."
        )
    if not r["ladders"].get("BULLET"):
        out.append("no BULLET_n roles — a bullet-heavy report needs a complete bullet ladder.")
    return out
