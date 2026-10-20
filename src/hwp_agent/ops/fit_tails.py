"""Widow-tail fitting (issue #39).

Shorten paragraph widow tail lines by slightly reducing char spacing and/or ratio.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from lxml import etree

from ..render.rhwp import resolve_rhwp
from .container import _rewrite_zip_preserving

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


def _hh(tag: str) -> str:
    return f"{{{_HH}}}{tag}"


_LANG_ATTRS = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")


@dataclass(frozen=True)
class TailFitCandidate:
    section: str  # e.g. "Contents/section0.xml"
    para_index: int  # 0-based top-level index of <hp:p> in its section
    page_num: int | None  # 1-based page number if available
    text_preview: str  # first 40 chars of paragraph
    total_lines: int  # visual lines count (>= 2)
    target_lines: int  # fitted lines count (if fittable, total_lines - 1)
    total_chars: int  # total character count of paragraph
    tail_chars: int  # character count of the last line (1..max_chars)
    orig_spacing: int  # original spacing (e.g. 0)
    orig_ratio: int  # original ratio (e.g. 100)
    target_spacing: int  # fitted spacing (e.g. -3)
    target_ratio: int  # fitted ratio (e.g. 100)
    fittable: bool  # True if fits within (min_spacing, min_ratio) and reduces line count

    def as_dict(self) -> dict:
        return asdict(self)


def _generate_fit_steps(
    orig_spacing: int = 0,
    orig_ratio: int = 100,
    min_spacing: int = -5,
    min_ratio: int = 95,
) -> list[tuple[int, int]]:
    """Generate (spacing, ratio) steps reducing spacing first, then ratio."""
    steps: list[tuple[int, int]] = []
    # 1. Reduce spacing (1% step) down to min_spacing
    for sp in range(orig_spacing - 1, min_spacing - 1, -1):
        steps.append((sp, orig_ratio))
    # 2. Reduce ratio (1% step) down to min_ratio
    for rat in range(orig_ratio - 1, min_ratio - 1, -1):
        steps.append((min_spacing, rat))
    return steps


def compute_tail_fit(
    total_chars: int,
    tail_chars: int,
    orig_spacing: int = 0,
    orig_ratio: int = 100,
    min_spacing: int = -5,
    min_ratio: int = 95,
) -> tuple[int, int, bool]:
    """Compute (target_spacing, target_ratio, fittable) using heuristic ratio.

    Used as fallback when rhwp binary is unavailable.
    """
    if total_chars <= 0 or tail_chars <= 0:
        return orig_spacing, orig_ratio, False

    req_reduction = (tail_chars / total_chars) * 100.0

    target_spacing = orig_spacing
    target_ratio = orig_ratio
    curr_reduction = 0.0

    # 1. Reduce spacing (1% step) down to min_spacing
    while target_spacing > min_spacing and curr_reduction < req_reduction:
        target_spacing -= 1
        curr_reduction += 1.0

    # 2. Reduce ratio (1% step) down to min_ratio
    while target_ratio > min_ratio and curr_reduction < req_reduction:
        target_ratio -= 1
        curr_reduction += 1.0

    fittable = curr_reduction >= req_reduction
    return target_spacing, target_ratio, fittable


def _para_text(p: etree.Element) -> str:
    """Extract plain text from paragraph's direct runs (not nested tables)."""
    parts: list[str] = []
    for run in p.findall(_hp("run")):
        for child in run:
            if child.tag == _hp("t"):
                if child.text:
                    parts.append(child.text)
                for sub in child:
                    if sub.tag == _hp("lineBreak"):
                        parts.append("\n")
                    if sub.tail:
                        parts.append(sub.tail)
            elif child.tag == _hp("lineBreak"):
                parts.append("\n")
    return "".join(parts)


def _section_number(name: str) -> int:
    m = re.search(r"section(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def _section_parts(names: list[str]) -> list[str]:
    parts = [n for n in names if re.search(r"Contents/section\d+\.xml$", n)]
    return sorted(parts, key=_section_number)


def _heading_identifiers(header_xml: str) -> tuple[set[str], set[str]]:
    """Return (heading_style_ids, heading_para_pr_ids) from header XML.

    Only OUTLINE (structural headings) are excluded. BULLET paragraphs are
    institutional body lists and MUST be included in fitting.
    """
    if not header_xml:
        return set(), set()
    root = etree.fromstring(header_xml.encode("utf-8"))

    heading_para_pr_ids: set[str] = set()
    for pp in root.iter(_hh("paraPr")):
        pp_id = pp.get("id")
        heading = pp.find(_hh("heading"))
        if heading is not None:
            htype = heading.get("type", "NONE")
            if htype == "OUTLINE":
                if pp_id:
                    heading_para_pr_ids.add(pp_id)

    heading_style_ids: set[str] = set()
    for st in root.iter(_hh("style")):
        st_id = st.get("id")
        name = st.get("name", "")
        pp_id = st.get("paraPrIDRef")
        if pp_id and pp_id in heading_para_pr_ids:
            if st_id:
                heading_style_ids.add(st_id)
        elif any(
            k in name.lower()
            for k in ("개요", "제목", "heading", "title", "ai:h", "ai:heading")
        ) and not any(k in name.lower() for k in ("bullet", "글머리")):
            if st_id:
                heading_style_ids.add(st_id)

    return heading_style_ids, heading_para_pr_ids


def _char_pr_props(char_pr: etree.Element) -> tuple[int, int]:
    """Return (spacing, ratio) for Hangul or first defined language in charPr."""
    sp_elem = char_pr.find(_hh("spacing"))
    rat_elem = char_pr.find(_hh("ratio"))
    spacing = 0
    ratio = 100
    if sp_elem is not None:
        val = sp_elem.get("hangul") or next(iter(sp_elem.attrib.values()), "0")
        try:
            spacing = int(val)
        except ValueError:
            spacing = 0
    if rat_elem is not None:
        val = rat_elem.get("hangul") or next(iter(rat_elem.attrib.values()), "100")
        try:
            ratio = int(val)
        except ValueError:
            ratio = 100
    return spacing, ratio


def _get_rhwp_para_lines(hwpx_path: str | Path, rhwp_bin: str) -> dict[tuple[int, int], int]:
    """Parse paragraph visual line counts from `rhwp dump-pages`."""
    res = subprocess.run([rhwp_bin, "dump-pages", str(hwpx_path)], capture_output=True, text=True)
    if res.returncode != 0:
        return {}

    sec_re = re.compile(r"section=(\d+)")
    p_re = re.compile(r"\b(?:FullParagraph|PartialParagraph)\s+pi=(\d+).*?\bls=(\d+)\[")
    cur_sec = 0
    lines_map: dict[tuple[int, int], int] = {}
    for line in res.stdout.splitlines():
        sm = sec_re.search(line)
        if sm and "=== 페이지" in line:
            cur_sec = int(sm.group(1))
        pm = p_re.search(line)
        if pm:
            pi = int(pm.group(1))
            ls = int(pm.group(2))
            lines_map[(cur_sec, pi)] = lines_map.get((cur_sec, pi), 0) + ls
    return lines_map


def _clone_char_pr(
    char_pr: etree.Element, new_id: str, spacing: int, ratio: int
) -> etree.Element:
    """Deep-copy charPr, set new id, and update spacing/ratio attributes."""
    clone = copy.deepcopy(char_pr)
    clone.set("id", str(new_id))

    sp = clone.find(_hh("spacing"))
    if sp is None:
        sp = etree.SubElement(clone, _hh("spacing"))
    for attr in _LANG_ATTRS:
        sp.set(attr, str(spacing))

    rat = clone.find(_hh("ratio"))
    if rat is None:
        rat = etree.SubElement(clone, _hh("ratio"))
    for attr in _LANG_ATTRS:
        rat.set(attr, str(ratio))

    return clone


class _CharPrCloner:
    """Manages cloning of charPr elements preserving original font/size attributes."""

    def __init__(self, char_props_elem: etree.Element):
        self.char_props = char_props_elem
        self.existing = char_props_elem.findall(_hh("charPr"))
        self.by_id = {cp.get("id"): cp for cp in self.existing if cp.get("id")}
        self.next_id = len(self.existing)
        self.cache: dict[tuple[str, int, int], str] = {}

    def get_or_create(self, orig_id: str, spacing: int, ratio: int) -> str:
        key = (orig_id, spacing, ratio)
        if key in self.cache:
            return self.cache[key]
        base_pr = self.by_id.get(orig_id)
        if base_pr is None:
            base_pr = self.existing[0] if self.existing else None
        new_id_str = str(self.next_id)
        new_pr = _clone_char_pr(base_pr, new_id_str, spacing, ratio)
        self.char_props.append(new_pr)
        self.by_id[new_id_str] = new_pr
        self.cache[key] = new_id_str
        self.next_id += 1
        return new_id_str

    def finalize(self) -> None:
        self.char_props.set("itemCnt", str(self.next_id))


def detect_tail_fits(
    src: str | Path,
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
    include_footnotes: bool = False,
    rhwp_bin: str | None = None,
) -> list[TailFitCandidate]:
    """Find paragraphs with widow tail lines and verify reduction with rhwp."""
    src = Path(src)
    resolved_rhwp = resolve_rhwp(rhwp_bin)

    with zipfile.ZipFile(str(src)) as z:
        names = z.namelist()
        header_xml = (
            z.read("Contents/header.xml").decode("utf-8")
            if "Contents/header.xml" in names
            else ""
        )
        sections = _section_parts(names)
        section_blobs = {s: z.read(s) for s in sections}

    heading_style_ids, heading_para_pr_ids = _heading_identifiers(header_xml)

    header_root = (
        etree.fromstring(header_xml.encode("utf-8")) if header_xml else None
    )
    char_prs: dict[str, etree.Element] = {}
    if header_root is not None:
        for cp in header_root.iter(_hh("charPr")):
            cid = cp.get("id")
            if cid:
                char_prs[cid] = cp

    # Baseline line counts from rhwp if available
    baseline_lines = _get_rhwp_para_lines(src, resolved_rhwp) if resolved_rhwp else {}

    raw_candidates: list[dict] = []

    for section_name in sections:
        s_idx = _section_number(section_name)
        root = etree.fromstring(section_blobs[section_name])
        top_paragraphs = root.findall(_hp("p"))
        top_pi_map = {id(p): pi for pi, p in enumerate(top_paragraphs)}

        for p_idx, p in enumerate(root.iter(_hp("p"))):
            # Exclude table cells
            if any(anc.tag == _hp("tc") for anc in p.iterancestors()):
                continue
            # Exclude footnotes / subLists unless requested
            if not include_footnotes and any(
                anc.tag == _hp("subList") for anc in p.iterancestors()
            ):
                continue
            # Exclude headings
            st_ref = p.get("styleIDRef")
            pp_ref = p.get("paraPrIDRef")
            if (st_ref and st_ref in heading_style_ids) or (
                pp_ref and pp_ref in heading_para_pr_ids
            ):
                continue

            # Check linesegarray
            linesegs = p.findall(f"{_hp('linesegarray')}/{_hp('lineseg')}")
            if len(linesegs) < 2:
                continue

            text = _para_text(p)
            total_chars = len(text)
            if total_chars == 0:
                continue

            last_seg = linesegs[-1]
            try:
                last_pos = int(last_seg.get("textpos", 0))
            except ValueError:
                continue

            tail_chars = total_chars - last_pos
            if not (1 <= tail_chars <= max_chars):
                continue

            # Skip tails created by an explicit lineBreak
            if text[:last_pos].endswith("\n") or "\n" in text[last_pos:]:
                continue

            # Primary charPrIDRef
            char_pr_id = None
            for run in p.findall(_hp("run")):
                c_ref = run.get("charPrIDRef")
                if c_ref and c_ref in char_prs:
                    char_pr_id = c_ref
                    break

            orig_spacing = 0
            orig_ratio = 100
            if char_pr_id and char_pr_id in char_prs:
                orig_spacing, orig_ratio = _char_pr_props(char_prs[char_pr_id])

            top_pi = top_pi_map.get(id(p))
            if top_pi is not None and (s_idx, top_pi) in baseline_lines:
                orig_lines = baseline_lines[(s_idx, top_pi)]
            else:
                orig_lines = len(linesegs)

            raw_candidates.append({
                "s_idx": s_idx,
                "top_pi": top_pi,
                "section": section_name,
                "para_index": p_idx,
                "text_preview": text[:40].replace("\n", " "),
                "total_lines": orig_lines,
                "total_chars": total_chars,
                "tail_chars": tail_chars,
                "orig_spacing": orig_spacing,
                "orig_ratio": orig_ratio,
            })

    if not raw_candidates:
        return []

    # If rhwp is not available, fall back to heuristic ratio
    if not resolved_rhwp:
        candidates: list[TailFitCandidate] = []
        for c in raw_candidates:
            target_sp, target_rat, fittable = compute_tail_fit(
                total_chars=c["total_chars"],
                tail_chars=c["tail_chars"],
                orig_spacing=c["orig_spacing"],
                orig_ratio=c["orig_ratio"],
                min_spacing=min_spacing,
                min_ratio=min_ratio,
            )
            target_lines = c["total_lines"] - 1 if fittable else c["total_lines"]
            candidates.append(
                TailFitCandidate(
                    section=c["section"],
                    para_index=c["para_index"],
                    page_num=None,
                    text_preview=c["text_preview"],
                    total_lines=c["total_lines"],
                    target_lines=target_lines,
                    total_chars=c["total_chars"],
                    tail_chars=c["tail_chars"],
                    orig_spacing=c["orig_spacing"],
                    orig_ratio=c["orig_ratio"],
                    target_spacing=target_sp,
                    target_ratio=target_rat,
                    fittable=fittable,
                )
            )
        return candidates

    # RHWP-driven step-wise simulation
    # Step 1: Fast batch scan at max limit (min_spacing, min_ratio) to filter unfittable paras
    with tempfile.TemporaryDirectory() as td:
        tmp_max = Path(td) / "batch_max.hwpx"
        h_root = etree.fromstring(header_xml.encode("utf-8"))
        cps = h_root.find(".//" + _hh("charProperties"))
        if cps is not None:
            cloner_max = _CharPrCloner(cps)
            overrides = {}
            by_sec: dict[str, list[dict]] = {}
            for rc in raw_candidates:
                by_sec.setdefault(rc["section"], []).append(rc)
            for sname, cands in by_sec.items():
                s_root = etree.fromstring(section_blobs[sname])
                all_p = list(s_root.iter(_hp("p")))
                cand_by_idx = {c["para_index"]: c for c in cands}
                for pi, p in enumerate(all_p):
                    if pi not in cand_by_idx:
                        continue
                    for r in p.findall(_hp("run")):
                        orig_cid = r.get("charPrIDRef") or "0"
                        new_cid = cloner_max.get_or_create(
                            orig_cid, min_spacing, min_ratio
                        )
                        r.set("charPrIDRef", new_cid)
                    lsa = p.find(_hp("linesegarray"))
                    if lsa is not None:
                        p.remove(lsa)
                overrides[sname] = (
                    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    + etree.tostring(s_root, encoding="UTF-8")
                )
            cloner_max.finalize()
            overrides["Contents/header.xml"] = (
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                + etree.tostring(h_root, encoding="UTF-8")
            )
            _rewrite_zip_preserving(src, tmp_max, overrides)
            max_lines = _get_rhwp_para_lines(tmp_max, resolved_rhwp)
        else:
            max_lines = {}

    candidates = []
    # Identify survivors that reduce line count at max limit
    for c in raw_candidates:
        s_idx = c["s_idx"]
        top_pi = c["top_pi"]
        p_idx = c["para_index"]
        orig_l = c["total_lines"]

        # If it's a footnote paragraph (top_pi is None), rhwp does not track it individually.
        # Fall back to compute_tail_fit heuristic.
        if top_pi is None:
            target_sp, target_rat, fittable = compute_tail_fit(
                total_chars=c["total_chars"],
                tail_chars=c["tail_chars"],
                orig_spacing=c["orig_spacing"],
                orig_ratio=c["orig_ratio"],
                min_spacing=min_spacing,
                min_ratio=min_ratio,
            )
            target_lines = orig_l - 1 if fittable else orig_l
            candidates.append(
                TailFitCandidate(
                    section=c["section"],
                    para_index=p_idx,
                    page_num=None,
                    text_preview=c["text_preview"],
                    total_lines=orig_l,
                    target_lines=target_lines,
                    total_chars=c["total_chars"],
                    tail_chars=c["tail_chars"],
                    orig_spacing=c["orig_spacing"],
                    orig_ratio=c["orig_ratio"],
                    target_spacing=target_sp,
                    target_ratio=target_rat,
                    fittable=fittable,
                )
            )
            continue

        at_max_l = max_lines.get((s_idx, top_pi), orig_l)

        if at_max_l >= orig_l:
            # Cannot reduce lines even at maximum allowed reduction
            candidates.append(
                TailFitCandidate(
                    section=c["section"],
                    para_index=p_idx,
                    page_num=None,
                    text_preview=c["text_preview"],
                    total_lines=orig_l,
                    target_lines=orig_l,
                    total_chars=c["total_chars"],
                    tail_chars=c["tail_chars"],
                    orig_spacing=c["orig_spacing"],
                    orig_ratio=c["orig_ratio"],
                    target_spacing=min_spacing,
                    target_ratio=min_ratio,
                    fittable=False,
                )
            )
            continue

        # Step 2: Search minimum step for this survivor
        steps = _generate_fit_steps(
            c["orig_spacing"], c["orig_ratio"], min_spacing, min_ratio
        )
        found_sp = min_spacing
        found_rat = min_ratio
        found_lines = at_max_l
        is_fitted = False

        for sp, rat in steps:
            with tempfile.TemporaryDirectory() as td:
                tmp_step = Path(td) / "step.hwpx"
                h_step = etree.fromstring(header_xml.encode("utf-8"))
                cps_step = h_step.find(".//" + _hh("charProperties"))
                cloner_step = _CharPrCloner(cps_step)

                s_step = etree.fromstring(section_blobs[c["section"]])
                all_p = list(s_step.iter(_hp("p")))
                p = all_p[p_idx]
                for r in p.findall(_hp("run")):
                    orig_cid = r.get("charPrIDRef") or "0"
                    new_cid = cloner_step.get_or_create(orig_cid, sp, rat)
                    r.set("charPrIDRef", new_cid)
                lsa = p.find(_hp("linesegarray"))
                if lsa is not None:
                    p.remove(lsa)
                cloner_step.finalize()

                ov = {
                    "Contents/header.xml": (
                        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                        + etree.tostring(h_step, encoding="UTF-8")
                    ),
                    c["section"]: (
                        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                        + etree.tostring(s_step, encoding="UTF-8")
                    ),
                }
                _rewrite_zip_preserving(src, tmp_step, ov)
                step_map = _get_rhwp_para_lines(tmp_step, resolved_rhwp)
                cur_l = step_map.get((s_idx, top_pi), orig_l)
                if cur_l < orig_l:
                    found_sp = sp
                    found_rat = rat
                    found_lines = cur_l
                    is_fitted = True
                    break

        candidates.append(
            TailFitCandidate(
                section=c["section"],
                para_index=p_idx,
                page_num=None,
                text_preview=c["text_preview"],
                total_lines=orig_l,
                target_lines=found_lines if is_fitted else orig_l,
                total_chars=c["total_chars"],
                tail_chars=c["tail_chars"],
                orig_spacing=c["orig_spacing"],
                orig_ratio=c["orig_ratio"],
                target_spacing=found_sp,
                target_ratio=found_rat,
                fittable=is_fitted,
            )
        )

    return candidates


def format_tail_fit_report(
    candidates: list[TailFitCandidate],
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
) -> str:
    """Format human-readable dry-run / audit report with before/after line counts."""
    if not candidates:
        return "꼬리 줄 압축(widow-tail fitting) 대상 문단 없음."

    fittable_count = sum(1 for c in candidates if c.fittable)
    unfittable_count = len(candidates) - fittable_count

    lines = [
        f"꼬리 줄 압축(widow-tail fitting) 대상 문단: 총 {len(candidates)}건 "
        f"(적용 가능: {fittable_count}건, 한도 초과: {unfittable_count}건)"
    ]
    for idx, c in enumerate(candidates, start=1):
        diff = c.target_lines - c.total_lines
        diff_str = f" ({diff}줄)" if diff != 0 else ""
        if c.fittable:
            status = (
                f"총 {c.total_lines}줄 -> {c.target_lines}줄{diff_str}, "
                f"자간 {c.target_spacing}%, 장평 {c.target_ratio}% (적용 가능)"
            )
        else:
            status = (
                f"총 {c.total_lines}줄 -> 한도 초과 "
                f"(자간 {min_spacing}%, 장평 {min_ratio}%로도 줄 수 유지, 미적용)"
            )
        page_info = f" (쪽 {c.page_num})" if c.page_num is not None else ""
        lines.append(
            f"  [{idx}] {c.section} #{c.para_index}{page_info}: \"{c.text_preview}\""
        )
        lines.append(
            f"      마지막 줄: {c.tail_chars}글자 -> {status}"
        )

    return "\n".join(lines)


def apply_tail_fits(
    src: str | Path,
    dst: str | Path,
    candidates: list[TailFitCandidate] | None = None,
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
    include_footnotes: bool = False,
    rhwp_bin: str | None = None,
) -> int:
    """Apply widow-tail fitting to the HWPX document.

    Only applies to fittable candidates verified to reduce lines.
    Clones charPr elements at the end of charProperties with updated itemCnt,
    updates run charPrIDRef, and drops the paragraph's linesegarray (issue #15).
    Returns number of paragraphs fitted.
    """
    src, dst = Path(src), Path(dst)
    if candidates is None:
        candidates = detect_tail_fits(
            src,
            max_chars=max_chars,
            min_spacing=min_spacing,
            min_ratio=min_ratio,
            include_footnotes=include_footnotes,
            rhwp_bin=rhwp_bin,
        )

    to_fit = [c for c in candidates if c.fittable]
    if not to_fit:
        if str(src) != str(dst):
            shutil.copyfile(src, dst)
        return 0

    # Group by section
    by_section: dict[str, dict[int, TailFitCandidate]] = {}
    for c in to_fit:
        by_section.setdefault(c.section, {})[c.para_index] = c

    with zipfile.ZipFile(str(src)) as z:
        header_xml = z.read("Contents/header.xml")
        section_blobs = {s: z.read(s) for s in by_section}

    header_root = etree.fromstring(header_xml)
    char_props = header_root.find(f".//{_hh('charProperties')}")
    if char_props is None:
        raise ValueError("Contents/header.xml missing charProperties")

    cloner = _CharPrCloner(char_props)
    overrides: dict[str, bytes] = {}
    fitted_count = 0

    for section_name, cand_map in by_section.items():
        sec_root = etree.fromstring(section_blobs[section_name])
        for p_idx, p in enumerate(sec_root.iter(_hp("p"))):
            if p_idx not in cand_map:
                continue
            cand = cand_map[p_idx]

            # Update runs with cloned charPr
            for run in p.findall(_hp("run")):
                c_ref = run.get("charPrIDRef") or "0"
                new_c_ref = cloner.get_or_create(
                    c_ref, cand.target_spacing, cand.target_ratio
                )
                run.set("charPrIDRef", new_c_ref)

            # Drop linesegarray (issue #15 reuse)
            lsa = p.find(_hp("linesegarray"))
            if lsa is not None:
                p.remove(lsa)

            fitted_count += 1

        decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        overrides[section_name] = decl + etree.tostring(
            sec_root, encoding="UTF-8", xml_declaration=False
        )

    # Update itemCnt in header.xml
    cloner.finalize()
    decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    overrides["Contents/header.xml"] = decl + etree.tostring(
        header_root, encoding="UTF-8", xml_declaration=False
    )

    tmp = dst.with_suffix(dst.suffix + ".tailfit.tmp")
    _rewrite_zip_preserving(src, tmp, overrides)
    tmp.replace(dst)

    return fitted_count
