"""Widow-tail fitting (issue #39).

Shorten paragraph widow tail lines by slightly reducing char spacing and/or ratio.
"""

from __future__ import annotations

import copy
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from lxml import etree

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
    para_index: int  # 0-based index of <hp:p> in its section
    page_num: int | None  # 1-based page number if available
    text_preview: str  # first 40 chars of paragraph
    total_lines: int  # visual lines count (>= 2)
    total_chars: int  # total character count of paragraph
    tail_chars: int  # character count of the last line (1..max_chars)
    orig_spacing: int  # original spacing (e.g. 0)
    orig_ratio: int  # original ratio (e.g. 100)
    target_spacing: int  # fitted spacing (e.g. -3)
    target_ratio: int  # fitted ratio (e.g. 100)
    fittable: bool  # True if fits within (min_spacing, min_ratio)

    def as_dict(self) -> dict:
        return asdict(self)


def compute_tail_fit(
    total_chars: int,
    tail_chars: int,
    orig_spacing: int = 0,
    orig_ratio: int = 100,
    min_spacing: int = -5,
    min_ratio: int = 95,
) -> tuple[int, int, bool]:
    """Compute (target_spacing, target_ratio, fittable) to pull back the widow tail.

    The required reduction ratio is tail_chars / total_chars (as percentage).
    We reduce spacing first (down to min_spacing), then ratio (down to min_ratio).
    Returns (target_spacing, target_ratio, fittable).
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


def _section_parts(names: list[str]) -> list[str]:
    return sorted(n for n in names if re.search(r"Contents/section\d+\.xml$", n))


def _heading_identifiers(header_xml: str) -> tuple[set[str], set[str]]:
    """Return (heading_style_ids, heading_para_pr_ids) from header XML."""
    if not header_xml:
        return set(), set()
    root = etree.fromstring(header_xml.encode("utf-8"))

    heading_para_pr_ids: set[str] = set()
    for pp in root.iter(_hh("paraPr")):
        pp_id = pp.get("id")
        heading = pp.find(_hh("heading"))
        if heading is not None:
            htype = heading.get("type", "NONE")
            if htype in ("OUTLINE", "BULLET"):
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
        ):
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


def detect_tail_fits(
    src: str | Path,
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
    include_footnotes: bool = False,
) -> list[TailFitCandidate]:
    """Find paragraphs with widow tail lines eligible for compression."""
    candidates: list[TailFitCandidate] = []
    src = Path(src)

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

    for section_name in sections:
        root = etree.fromstring(section_blobs[section_name])
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

            # Skip tails created by an explicit lineBreak — spacing/ratio adjustments
            # cannot pull text across a forced hard break.
            if text[:last_pos].endswith("\n") or "\n" in text[last_pos:]:
                continue

            # Find paragraph's primary charPrIDRef
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

            target_spacing, target_ratio, fittable = compute_tail_fit(
                total_chars=total_chars,
                tail_chars=tail_chars,
                orig_spacing=orig_spacing,
                orig_ratio=orig_ratio,
                min_spacing=min_spacing,
                min_ratio=min_ratio,
            )

            preview = text[:40].replace("\n", " ")
            candidates.append(
                TailFitCandidate(
                    section=section_name,
                    para_index=p_idx,
                    page_num=None,
                    text_preview=preview,
                    total_lines=len(linesegs),
                    total_chars=total_chars,
                    tail_chars=tail_chars,
                    orig_spacing=orig_spacing,
                    orig_ratio=orig_ratio,
                    target_spacing=target_spacing,
                    target_ratio=target_ratio,
                    fittable=fittable,
                )
            )

    return candidates


def format_tail_fit_report(
    candidates: list[TailFitCandidate],
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
) -> str:
    """Format human-readable dry-run / audit report."""
    if not candidates:
        return "꼬리 줄 압축(widow-tail fitting) 대상 문단 없음."

    fittable_count = sum(1 for c in candidates if c.fittable)
    unfittable_count = len(candidates) - fittable_count

    lines = [
        f"꼬리 줄 압축(widow-tail fitting) 대상 문단: 총 {len(candidates)}건 "
        f"(적용 가능: {fittable_count}건, 한도 초과: {unfittable_count}건)"
    ]
    for idx, c in enumerate(candidates, start=1):
        status = (
            f"자간 {c.target_spacing}%, 장평 {c.target_ratio}% (적용 가능)"
            if c.fittable
            else f"한도 초과 (자간 {min_spacing}%, 장평 {min_ratio}%로 부족, 미적용)"
        )
        page_info = f" (쪽 {c.page_num})" if c.page_num is not None else ""
        lines.append(
            f"  [{idx}] {c.section} #{c.para_index}{page_info}: \"{c.text_preview}\""
        )
        lines.append(
            f"      총 {c.total_lines}줄, 마지막 줄: {c.tail_chars}글자 -> {status}"
        )

    return "\n".join(lines)


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


def apply_tail_fits(
    src: str | Path,
    dst: str | Path,
    candidates: list[TailFitCandidate] | None = None,
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
    include_footnotes: bool = False,
) -> int:
    """Apply widow-tail fitting to the HWPX document.

    Only applies to fittable candidates. Clones charPr elements at the end
    of charProperties with updated itemCnt, updates run charPrIDRef, and drops
    the paragraph's linesegarray (issue #15).
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

    existing_char_prs = char_props.findall(_hh("charPr"))
    char_pr_by_id = {cp.get("id"): cp for cp in existing_char_prs if cp.get("id")}
    next_id = len(existing_char_prs)

    # Cache cloned charPr: (orig_id, target_spacing, target_ratio) -> new_id
    cloned_cache: dict[tuple[str, int, int], str] = {}

    def get_or_create_cloned_id(orig_id: str, spacing: int, ratio: int) -> str:
        nonlocal next_id
        key = (orig_id, spacing, ratio)
        if key in cloned_cache:
            return cloned_cache[key]
        base_pr = char_pr_by_id.get(orig_id)
        if base_pr is None:
            # Fallback: clone first charPr if orig_id missing
            base_pr = existing_char_prs[0]
        new_id_str = str(next_id)
        new_pr = _clone_char_pr(base_pr, new_id_str, spacing, ratio)
        char_props.append(new_pr)
        char_pr_by_id[new_id_str] = new_pr
        cloned_cache[key] = new_id_str
        next_id += 1
        return new_id_str

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
                new_c_ref = get_or_create_cloned_id(
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
    char_props.set("itemCnt", str(next_id))
    decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    overrides["Contents/header.xml"] = decl + etree.tostring(
        header_root, encoding="UTF-8", xml_declaration=False
    )

    tmp = dst.with_suffix(dst.suffix + ".tailfit.tmp")
    _rewrite_zip_preserving(src, tmp, overrides)
    tmp.replace(dst)

    return fitted_count
