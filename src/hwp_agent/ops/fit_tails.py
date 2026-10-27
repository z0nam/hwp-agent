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
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from lxml import etree

from ..render.config import resolve_hwp2pdf_config
from ..render.remote_hwp2pdf import Hwp2PdfClient, Hwp2PdfError
from ..render.rhwp import resolve_rhwp, rhwp_search_desc
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
    # Why a tail was left alone without trying: "layout_mismatch" when rhwp does
    # not reproduce the Hangul-saved layout, so its simulation can't be trusted.
    skip_reason: str | None = None
    # Engine this paragraph was judged on when one detection mixes engines
    # (#65: "hwp2pdf" or "rhwp"); None when the whole detection used one.
    engine: str | None = None

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
    eff_min_spacing = min(orig_spacing, min_spacing)
    eff_min_ratio = min(orig_ratio, min_ratio)
    # 1. Reduce spacing (1% step) down to eff_min_spacing
    for sp in range(orig_spacing - 1, eff_min_spacing - 1, -1):
        steps.append((sp, orig_ratio))
    # 2. Reduce ratio (1% step) down to eff_min_ratio
    base_sp = eff_min_spacing
    for rat in range(orig_ratio - 1, eff_min_ratio - 1, -1):
        steps.append((base_sp, rat))
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

    eff_min_spacing = min(orig_spacing, min_spacing)
    eff_min_ratio = min(orig_ratio, min_ratio)

    req_reduction = (tail_chars / total_chars) * 100.0

    target_spacing = orig_spacing
    target_ratio = orig_ratio
    curr_reduction = 0.0

    # 1. Reduce spacing (1% step) down to eff_min_spacing
    while target_spacing > eff_min_spacing and curr_reduction < req_reduction:
        target_spacing -= 1
        curr_reduction += 1.0

    # 2. Reduce ratio (1% step) down to eff_min_ratio
    while target_ratio > eff_min_ratio and curr_reduction < req_reduction:
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


def _plain_runs(p: etree.Element) -> bool:
    """True if the paragraph's runs hold only text and lineBreaks.

    linesegarray textpos counts inline controls (secPr, ctrl, tab, …) as extra
    code units that :func:`_para_text` does not see, so the last-line length of
    such a paragraph can't be computed from its text.
    """
    for run in p.findall(_hp("run")):
        for child in run:
            if child.tag != _hp("t"):
                return False
            if any(sub.tag != _hp("lineBreak") for sub in child):
                return False
    return True


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


@dataclass
class _RhwpPara:
    lines: int  # visual lines of the whole paragraph
    last_ts: int  # textpos of the paragraph's last line
    page_num: int | None  # page holding the last line


_PAGE_RE = re.compile(r"=== 페이지 (\d+) .*?\bsection=(\d+)")
_PARA_RE = re.compile(r"\b(?:FullParagraph|PartialParagraph)\s+pi=(\d+).*?\bls=(\d+)\[([^\]]*)\]")
_LAST_TS_RE = re.compile(r"(?:\blast ts|^ts)=(\d+)")


def _parse_dump_pages(stdout: str) -> dict[tuple[int, int], _RhwpPara]:
    """Per top-level paragraph layout from ``rhwp dump-pages`` output.

    A paragraph split across pages/columns appears once per piece
    (``PartialParagraph ... lines=a..b``), but every piece reports the whole
    paragraph's line array in ``ls=N[...]`` — so pieces are not summed; the
    last piece supplies the page of the last line.
    """
    cur_sec = 0
    cur_page: int | None = None
    out: dict[tuple[int, int], _RhwpPara] = {}
    for line in stdout.splitlines():
        sm = _PAGE_RE.search(line)
        if sm:
            cur_page = int(sm.group(1))  # physical page, not the per-section page_num
            cur_sec = int(sm.group(2))
            continue
        pm = _PARA_RE.search(line)
        if not pm:
            continue
        key = (cur_sec, int(pm.group(1)))
        tm = _LAST_TS_RE.search(pm.group(3))
        out[key] = _RhwpPara(
            lines=int(pm.group(2)),
            last_ts=int(tm.group(1)) if tm else 0,
            page_num=cur_page,
        )
    return out


def _rhwp_layout(
    hwpx_path: str | Path, rhwp_bin: str
) -> dict[tuple[int, int], _RhwpPara] | None:
    """Run ``rhwp dump-pages``; None if the subprocess fails or exits nonzero."""
    res = subprocess.run(  # noqa: S603
        [rhwp_bin, "dump-pages", str(hwpx_path)], capture_output=True, text=True
    )
    if res.returncode != 0:
        return None
    return _parse_dump_pages(res.stdout)


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


_XML_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _eligible_paragraphs(
    header_xml: str,
    section_blobs: dict[str, bytes],
    include_footnotes: bool,
) -> list[dict]:
    """Body paragraphs that tail fitting may touch, with their Hangul layout caches.

    Excludes table cells, outline headings, empty paragraphs, paragraphs with
    inline controls (see :func:`_plain_runs`), and footnote / subList paragraphs
    unless ``include_footnotes``.
    """
    heading_style_ids, heading_para_pr_ids = _heading_identifiers(header_xml)
    char_prs: dict[str, etree.Element] = {}
    if header_xml:
        for cp in etree.fromstring(header_xml.encode("utf-8")).iter(_hh("charPr")):
            cid = cp.get("id")
            if cid:
                char_prs[cid] = cp

    out: list[dict] = []
    for section_name, blob in section_blobs.items():
        s_idx = _section_number(section_name)
        root = etree.fromstring(blob)
        # Keep the list alive: lxml proxies (and so their id()) are only stable
        # while referenced.
        top_paragraphs = root.findall(_hp("p"))
        top_pi_map = {id(p): pi for pi, p in enumerate(top_paragraphs)}

        for p_idx, p in enumerate(root.iter(_hp("p"))):
            ancestors = {anc.tag for anc in p.iterancestors()}
            if _hp("tc") in ancestors:
                continue
            if not include_footnotes and _hp("subList") in ancestors:
                continue
            st_ref = p.get("styleIDRef")
            pp_ref = p.get("paraPrIDRef")
            if (st_ref and st_ref in heading_style_ids) or (
                pp_ref and pp_ref in heading_para_pr_ids
            ):
                continue
            text = _para_text(p)
            if not text or not _plain_runs(p):
                continue

            orig_spacing, orig_ratio = 0, 100
            for run in p.findall(_hp("run")):
                c_ref = run.get("charPrIDRef")
                if c_ref and c_ref in char_prs:
                    orig_spacing, orig_ratio = _char_pr_props(char_prs[c_ref])
                    break

            out.append({
                "s_idx": s_idx,
                "top_pi": top_pi_map.get(id(p)),
                "section": section_name,
                "para_index": p_idx,
                "text": text,
                "text_preview": text[:40].replace("\n", " "),
                "total_chars": len(text),
                "linesegs": p.findall(f"{_hp('linesegarray')}/{_hp('lineseg')}"),
                "orig_spacing": orig_spacing,
                "orig_ratio": orig_ratio,
            })
    return out


def _tail_chars(e: dict, last_pos: int, max_chars: int) -> int | None:
    """Last-line length if it is a widow tail (1..max_chars), else None.

    Tails produced by an explicit lineBreak are intentional and skipped.
    """
    text = e["text"]
    tail = len(text) - last_pos
    if not 1 <= tail <= max_chars:
        return None
    if text[:last_pos].endswith("\n") or "\n" in text[last_pos:]:
        return None
    return tail


def _hangul_tail(e: dict, max_chars: int) -> tuple[int, int] | None:
    """(lines, tail_chars) from the Hangul-saved linesegarray, if it is a widow tail."""
    linesegs = e["linesegs"]
    if len(linesegs) < 2:
        return None
    try:
        last_pos = int(linesegs[-1].get("textpos", 0))
    except ValueError:
        return None
    tail = _tail_chars(e, last_pos, max_chars)
    return (len(linesegs), tail) if tail else None


def _heuristic_candidate(
    e: dict, lines: int, tail: int, min_spacing: int, min_ratio: int
) -> TailFitCandidate:
    target_sp, target_rat, fittable = compute_tail_fit(
        total_chars=e["total_chars"],
        tail_chars=tail,
        orig_spacing=e["orig_spacing"],
        orig_ratio=e["orig_ratio"],
        min_spacing=min_spacing,
        min_ratio=min_ratio,
    )
    return TailFitCandidate(
        section=e["section"],
        para_index=e["para_index"],
        page_num=None,
        text_preview=e["text_preview"],
        total_lines=lines,
        target_lines=lines - 1 if fittable else lines,
        total_chars=e["total_chars"],
        tail_chars=tail,
        orig_spacing=e["orig_spacing"],
        orig_ratio=e["orig_ratio"],
        target_spacing=target_sp,
        target_ratio=target_rat,
        fittable=fittable,
    )


def _unverified_candidate(
    e: dict,
    lines: int,
    tail: int,
    lay: _RhwpPara | None,
    min_spacing: int,
    min_ratio: int,
) -> TailFitCandidate:
    return TailFitCandidate(
        section=e["section"],
        para_index=e["para_index"],
        page_num=lay.page_num if lay else None,
        text_preview=e["text_preview"],
        total_lines=lines,
        target_lines=lines,
        total_chars=e["total_chars"],
        tail_chars=tail,
        orig_spacing=e["orig_spacing"],
        orig_ratio=e["orig_ratio"],
        target_spacing=min(e["orig_spacing"], min_spacing),
        target_ratio=min(e["orig_ratio"], min_ratio),
        fittable=False,
        skip_reason="layout_mismatch",
    )


def _strip_linesegs(
    section_blobs: dict[str, bytes], para_indices: dict[str, set[int]]
) -> dict[str, bytes]:
    """Section blobs with the given paragraphs' linesegarray removed."""
    out = dict(section_blobs)
    for sname, indices in para_indices.items():
        root = etree.fromstring(section_blobs[sname])
        for p_idx, p in enumerate(root.iter(_hp("p"))):
            if p_idx in indices:
                lsa = p.find(_hp("linesegarray"))
                if lsa is not None:
                    p.remove(lsa)
        out[sname] = _XML_DECL + etree.tostring(root, encoding="UTF-8")
    return out


def _write_variant(
    src: Path,
    dst: Path,
    header_xml: str,
    section_blobs: dict[str, bytes],
    props: dict[str, dict[int, tuple[int, int]]],
) -> None:
    """Write ``src`` with ``section_blobs`` and per-paragraph (spacing, ratio) applied."""
    overrides: dict[str, bytes] = dict(section_blobs)
    if props:
        h_root = etree.fromstring(header_xml.encode("utf-8"))
        cloner = _CharPrCloner(h_root.find(".//" + _hh("charProperties")))
        for sname, by_idx in props.items():
            root = etree.fromstring(section_blobs[sname])
            for p_idx, p in enumerate(root.iter(_hp("p"))):
                if p_idx not in by_idx:
                    continue
                sp, rat = by_idx[p_idx]
                for r in p.findall(_hp("run")):
                    r.set("charPrIDRef", cloner.get_or_create(r.get("charPrIDRef") or "0", sp, rat))
            overrides[sname] = _XML_DECL + etree.tostring(root, encoding="UTF-8")
        cloner.finalize()
        overrides["Contents/header.xml"] = _XML_DECL + etree.tostring(h_root, encoding="UTF-8")
    _rewrite_zip_preserving(src, dst, overrides)


def _detect_with_rhwp(
    src: Path,
    header_xml: str,
    section_blobs: dict[str, bytes],
    eligible: list[dict],
    rhwp_bin: str,
    max_chars: int,
    min_spacing: int,
    min_ratio: int,
) -> list[TailFitCandidate] | None:
    """Verify tails with rhwp under like-for-like conditions. None if the baseline fails.

    Every measurement (the baseline and each adjusted variant) is taken with
    the eligible paragraphs' linesegarray removed, so rhwp lays them all out
    itself and variants differ from the baseline only in the adjusted charPr.
    Comparing against the Hangul-saved linesegarray instead would credit the
    lineseg removal (applied anyway when fitting) as a spacing/ratio effect.

    A Hangul-saved tail is simulated only when rhwp's baseline reproduces that
    paragraph's saved layout (same line count and last-line textpos); otherwise
    rhwp is not a faithful proxy for Hangul there, and the tail is reported as
    ``skip_reason="layout_mismatch"`` without being touched. Paragraphs with no
    saved layout are judged on rhwp's layout alone.
    """
    if not header_xml or etree.fromstring(header_xml.encode("utf-8")).find(
        ".//" + _hh("charProperties")
    ) is None:
        return None

    body = [e for e in eligible if e["top_pi"] is not None]
    strip: dict[str, set[int]] = {}
    for e in body:
        strip.setdefault(e["section"], set()).add(e["para_index"])
    control_blobs = _strip_linesegs(section_blobs, strip)

    with tempfile.TemporaryDirectory() as td:
        n = 0

        def measure(props: dict[str, dict[int, tuple[int, int]]]):
            nonlocal n
            n += 1
            path = Path(td) / f"variant{n}.hwpx"
            _write_variant(src, path, header_xml, control_blobs, props)
            return _rhwp_layout(path, rhwp_bin)

        baseline = measure({})
        if baseline is None:
            return None

        cands: list[dict] = []
        mismatched: list[TailFitCandidate] = []
        for e in body:
            lay = baseline.get((e["s_idx"], e["top_pi"]))
            if e["linesegs"]:
                # The tail the user sees is Hangul's. Verify it with rhwp only if
                # rhwp breaks the paragraph exactly as the saved linesegarray does.
                hangul = _hangul_tail(e, max_chars)
                if hangul is None:
                    continue
                lines, tail = hangul
                last_pos = int(e["linesegs"][-1].get("textpos", 0))
                if lay is None or lay.lines != lines or lay.last_ts != last_pos:
                    mismatched.append(
                        _unverified_candidate(e, lines, tail, lay, min_spacing, min_ratio)
                    )
                    continue
            else:
                # No saved layout to honour: rhwp's layout is the only one.
                if lay is None or lay.lines < 2:
                    continue
                lines, tail = lay.lines, _tail_chars(e, lay.last_ts, max_chars)
                if not tail:
                    continue
            cands.append({
                **e,
                "lines": lines,
                "tail": tail,
                "page_num": lay.page_num,
                "steps": _generate_fit_steps(
                    e["orig_spacing"], e["orig_ratio"], min_spacing, min_ratio
                ),
            })

        def lines_at(layout, c: dict) -> int:
            if layout is None:
                return c["lines"]
            lay = layout.get((c["s_idx"], c["top_pi"]))
            return lay.lines if lay else c["lines"]

        # Survivors: paragraphs that lose a line at the strongest allowed step.
        # Tail paragraphs reflow independently, so they share one variant per step.
        at_max: dict[str, dict[int, tuple[int, int]]] = {}
        for c in cands:
            if c["steps"]:
                at_max.setdefault(c["section"], {})[c["para_index"]] = c["steps"][-1]
        max_layout = measure(at_max) if at_max else None
        pending = [c for c in cands if c["steps"] and lines_at(max_layout, c) < c["lines"]]

        # Smallest step that actually removes a line.
        found: dict[int, tuple[int, int, int]] = {}
        for k in range(max((len(c["steps"]) for c in pending), default=0)):
            batch = [c for c in pending if id(c) not in found and k < len(c["steps"])]
            if not batch:
                break
            props: dict[str, dict[int, tuple[int, int]]] = {}
            for c in batch:
                props.setdefault(c["section"], {})[c["para_index"]] = c["steps"][k]
            layout = measure(props)
            for c in batch:
                cur = lines_at(layout, c)
                if cur < c["lines"]:
                    found[id(c)] = (*c["steps"][k], cur)

    out: list[TailFitCandidate] = mismatched
    for c in cands:
        hit = found.get(id(c))
        if hit:
            sp, rat, lines = hit
        else:
            sp = min(c["orig_spacing"], min_spacing)
            rat = min(c["orig_ratio"], min_ratio)
            lines = c["lines"]
        out.append(
            TailFitCandidate(
                section=c["section"],
                para_index=c["para_index"],
                page_num=c["page_num"],
                text_preview=c["text_preview"],
                total_lines=c["lines"],
                target_lines=lines,
                total_chars=c["total_chars"],
                tail_chars=c["tail"],
                orig_spacing=c["orig_spacing"],
                orig_ratio=c["orig_ratio"],
                target_spacing=sp,
                target_ratio=rat,
                fittable=hit is not None,
            )
        )
    return out


#: Which layout a detection was judged on (``TailFitDetection.engine``).
ENGINE_LABELS = {
    "document": "문서에 저장된 한글 조판(linesegarray)",
    "hwp2pdf": "hwp2pdf 한글 조판(다시 저장한 HWPX)",
    "rhwp": "rhwp 조판 기준(한글과 다를 수 있음)",
    "none": "판정 엔진 없음",
}


@dataclass
class TailFitDetection:
    """Candidates plus the engine they were judged on (issue #61).

    ``engine == "none"`` means the document could not be judged at all — not
    "no tails". ``notes`` holds one line per engine that was skipped and why.

    ``counts`` (hwp2pdf only, #65) splits the eligible paragraphs by how they
    were judged: ``hwp2pdf`` (Hangul's re-saved layout), ``rhwp`` (Hangul saved
    no layout for them, rhwp judged), ``no_layout`` (no layout and rhwp could not
    judge) and ``unmatched`` (text differs in the re-saved file).
    """

    candidates: list[TailFitCandidate]
    engine: str  # "document" | "hwp2pdf" | "rhwp" | "none"
    notes: list[str]
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def judged(self) -> bool:
        return self.engine != "none"

    @property
    def partial(self) -> bool:
        """Judged, but some eligible paragraphs were left unjudged."""
        return self.counts.get("no_layout", 0) + self.counts.get("unmatched", 0) > 0

    @property
    def coverage(self) -> str:
        """Paragraphs per engine, e.g. ``한글 22 · rhwp 57`` (empty if not split)."""
        if not self.counts:
            return ""
        parts = [f"한글 {self.counts.get('hwp2pdf', 0)}"]
        for key, label in (("rhwp", "rhwp"), ("no_layout", "판정 못함"),
                           ("unmatched", "맞출 수 없음")):
            if self.counts.get(key):
                parts.append(f"{label} {self.counts[key]}")
        return " · ".join(parts)

    @property
    def engine_label(self) -> str:
        label = ENGINE_LABELS[self.engine]
        return f"{label} — 문단 {self.coverage}" if self.coverage else label


def usable_hwp2pdf(
    hwp2pdf: Hwp2PdfClient | None, use_hwp2pdf: bool, notes: list[str]
) -> Hwp2PdfClient | None:
    """The hwp2pdf client to judge with, or None with the reason added to *notes*.

    Skipped when told not to, not configured, unreachable, or the node overlaps
    a running Hangul / queued job (#61) — never forced.
    """
    if not use_hwp2pdf:
        notes.append("hwp2pdf 미사용 — 호출하지 않도록 지정됨")
        return None
    try:
        client = hwp2pdf or Hwp2PdfClient(resolve_hwp2pdf_config())
    except ValueError as exc:  # malformed hwp2pdf.json: hwp2pdf is unavailable
        notes.append(f"hwp2pdf 미사용 — 설정 오류: {exc}")
        return None
    st = client.status("hwpx")
    if not st.usable:
        notes.append(f"hwp2pdf 미사용 — {st.reason}")
        return None
    return client


def hwp2pdf_resave_sections(
    client: Hwp2PdfClient, src: Path, section_names: list[str]
) -> dict[str, bytes]:
    """Re-save *src* through hwp2pdf (Hangul) and return the re-saved section
    XML blobs. The re-saved file is only measured, never returned."""
    with tempfile.TemporaryDirectory() as td:
        resaved = Path(td) / "resaved.hwpx"
        client.convert(src, resaved, "hwpx")
        try:
            with zipfile.ZipFile(str(resaved)) as z:
                names = set(z.namelist())
                return {s: z.read(s) for s in section_names if s in names}
        except zipfile.BadZipFile as exc:
            raise Hwp2PdfError(f"hwp2pdf가 돌려준 HWPX를 열 수 없음: {exc}") from None


def _hwp2pdf_linesegs(
    client: Hwp2PdfClient, src: Path, section_names: list[str]
) -> dict[str, list[tuple[str, list]]]:
    """Re-save *src* through hwp2pdf (Hangul) and read back each paragraph's
    ``(text, linesegs)`` per section, in document order."""
    blobs = hwp2pdf_resave_sections(client, src, section_names)
    out: dict[str, list[tuple[str, list]]] = {}
    for sname, blob in blobs.items():
        root = etree.fromstring(blob)
        out[sname] = [
            (_para_text(p), p.findall(f"{_hp('linesegarray')}/{_hp('lineseg')}"))
            for p in root.iter(_hp("p"))
        ]
    return out


def _with_hangul_layout(
    eligible: list[dict], layout: dict[str, list[tuple[str, list]]]
) -> tuple[list[dict], list[dict], int]:
    """Match eligible paragraphs to the re-saved file and its Hangul linesegs.

    Matched by position when the text agrees, else by text if that text is
    unique in the re-saved section. Returns ``(laid_out, unlaid, unmatched)``:
    paragraphs carrying the re-saved linesegs; paragraphs matched but saved
    without a linesegarray (Hangul lays out only the first pages before saving,
    #65); and the count matched to nothing.
    """
    ambiguous: list = []  # sentinel: duplicate text, or not found
    by_text: dict[str, dict[str, list]] = {}
    for sname, paras in layout.items():
        idx: dict[str, list] = {}
        for text, segs in paras:
            idx[text] = ambiguous if text in idx else segs
        by_text[sname] = idx
    laid: list[dict] = []
    unlaid: list[dict] = []
    missed = 0
    for e in eligible:
        paras = layout.get(e["section"], [])
        if e["para_index"] < len(paras) and paras[e["para_index"]][0] == e["text"]:
            segs = paras[e["para_index"]][1]
        else:
            segs = by_text.get(e["section"], {}).get(e["text"], ambiguous)
        if segs is ambiguous:
            missed += 1
        elif segs:
            laid.append({**e, "linesegs": segs})
        else:
            unlaid.append(e)
    return laid, unlaid, missed


def _judge_unlaid_with_rhwp(
    src: Path,
    header_xml: str,
    section_blobs: dict[str, bytes],
    unlaid: list[dict],
    rhwp_bin: str | None,
    rhwp_arg: str | None,
    max_chars: int,
    min_spacing: int,
    min_ratio: int,
) -> tuple[list[TailFitCandidate], int, str]:
    """Judge with rhwp the paragraphs hwp2pdf re-saved without a layout (#65).

    They go through the regular rhwp path (:func:`_detect_with_rhwp`), so their
    spacing/ratio come from rhwp measurements, not the Hangul heuristic.
    Returns ``(candidates, paragraphs judged, note)``.
    """
    lead = f"hwp2pdf 문서에 줄 배치가 없는 문단 {len(unlaid)}개"
    body = [e for e in unlaid if e["top_pi"] is not None]
    if rhwp_bin is None:
        return [], 0, f"{lead}는 판정 못함 — rhwp 없음 — {rhwp_search_desc(rhwp_arg)}"
    if not body:
        return [], 0, f"{lead}는 판정 못함 — 본문 밖(각주) 문단은 rhwp가 문단별로 보고하지 않음"
    try:
        cands = _detect_with_rhwp(
            src, header_xml, section_blobs, body, rhwp_bin, max_chars, min_spacing, min_ratio
        )
    except OSError as exc:  # not executable, bad interpreter: keep the Hangul results
        return [], 0, f"{lead}는 판정 못함 — rhwp 실행 실패 ({rhwp_bin}: {exc.strerror or exc})"
    if cands is None:
        return [], 0, f"{lead}는 판정 못함 — rhwp 실행 실패 ({rhwp_bin} dump-pages)"
    if len(body) == len(unlaid):
        note = f"{lead}는 rhwp로 판정 (한글이 앞쪽만 조판해 저장함)"
    else:
        note = (
            f"{lead} 중 {len(body)}개는 rhwp로 판정 (한글이 앞쪽만 조판해 저장함), "
            f"본문 밖(각주) {len(unlaid) - len(body)}개는 판정 못함"
        )
    return [replace(c, engine="rhwp") for c in cands], len(body), note


def _detect_saved_layout(
    src: Path,
    header_xml: str,
    section_blobs: dict[str, bytes],
    eligible: list[dict],
    rhwp_bin: str | None,
    max_chars: int,
    min_spacing: int,
    min_ratio: int,
) -> list[TailFitCandidate] | None:
    """Judge on linesegarray, verified with rhwp when available (pre-#61 path).

    Returns None only when rhwp was asked to lay out paragraphs and failed.
    """
    rhwp_cands: list[TailFitCandidate] | None = None
    if rhwp_bin and any(e["top_pi"] is not None for e in eligible):
        rhwp_cands = _detect_with_rhwp(
            src, header_xml, section_blobs, eligible, rhwp_bin,
            max_chars, min_spacing, min_ratio,
        )
    candidates: list[TailFitCandidate] = []
    for e in eligible:
        if rhwp_cands is not None and e["top_pi"] is not None:
            continue
        hangul = _hangul_tail(e, max_chars)
        if hangul:
            candidates.append(_heuristic_candidate(e, *hangul, min_spacing, min_ratio))
    if rhwp_cands:
        candidates.extend(rhwp_cands)
    return candidates


def detect_tail_fits_with_engine(
    src: str | Path,
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
    include_footnotes: bool = False,
    rhwp_bin: str | None = None,
    hwp2pdf: Hwp2PdfClient | None = None,
    use_hwp2pdf: bool = True,
) -> TailFitDetection:
    """Find widow tails, choosing the layout engine (issue #61).

    1. Every eligible paragraph has a Hangul-saved linesegarray → judge on it
       (verified with rhwp when available), as before.
    2. Otherwise → re-save the document as HWPX through hwp2pdf (Hangul) and
       judge on the linesegarray Hangul wrote. hwp2pdf is skipped when it is not
       configured, unreachable, or the node overlaps a running Hangul / queued job.
    3. Otherwise → rhwp's own layout ("rhwp 조판 기준", may differ from Hangul).
    4. Otherwise → ``engine="none"``: nothing judged, nothing to apply.

    Only the original document is ever modified by :func:`apply_tail_fits`; the
    re-saved HWPX is measured in a temp dir and discarded.
    """
    src = Path(src)
    with zipfile.ZipFile(str(src)) as z:
        names = z.namelist()
        header_xml = (
            z.read("Contents/header.xml").decode("utf-8")
            if "Contents/header.xml" in names
            else ""
        )
        section_blobs = {s: z.read(s) for s in _section_parts(names)}

    eligible = _eligible_paragraphs(header_xml, section_blobs, include_footnotes)
    resolved_rhwp = resolve_rhwp(rhwp_bin)

    def done(
        cands: list[TailFitCandidate],
        engine: str,
        notes: list[str],
        counts: dict[str, int] | None = None,
    ) -> TailFitDetection:
        cands.sort(key=lambda c: (_section_number(c.section), c.para_index))
        return TailFitDetection(cands, engine, notes, counts or {})

    if all(e["linesegs"] for e in eligible):
        cands = _detect_saved_layout(
            src, header_xml, section_blobs, eligible, resolved_rhwp,
            max_chars, min_spacing, min_ratio,
        )
        return done(cands, "document", [])

    notes: list[str] = []
    client = usable_hwp2pdf(hwp2pdf, use_hwp2pdf, notes)
    if client is not None:
        try:
            layout = _hwp2pdf_linesegs(client, src, list(section_blobs))
            laid, unlaid, missed = _with_hangul_layout(eligible, layout)
            if not laid and not unlaid:
                raise Hwp2PdfError("다시 저장한 문서의 문단을 원본과 맞출 수 없음")
            if not laid:
                raise Hwp2PdfError("다시 저장한 문서에 줄 배치(linesegarray)가 없음")
            cands = []
            for e in laid:
                hangul = _hangul_tail(e, max_chars)
                if hangul:
                    cands.append(replace(
                        _heuristic_candidate(e, *hangul, min_spacing, min_ratio),
                        engine="hwp2pdf",
                    ))
            counts = {"hwp2pdf": len(laid), "rhwp": 0, "no_layout": len(unlaid),
                      "unmatched": missed}
            if unlaid:
                rhwp_cands, n_rhwp, note = _judge_unlaid_with_rhwp(
                    src, header_xml, section_blobs, unlaid, resolved_rhwp, rhwp_bin,
                    max_chars, min_spacing, min_ratio,
                )
                cands += rhwp_cands
                counts["rhwp"] = n_rhwp
                counts["no_layout"] -= n_rhwp
                notes.append(note)
            if missed:
                notes.append(
                    f"hwp2pdf 문서와 텍스트가 맞지 않는 문단 {missed}개는 판정에서 제외"
                )
            return done(cands, "hwp2pdf", notes, counts)
        except Hwp2PdfError as exc:
            notes.append(f"hwp2pdf 판정 실패 — {exc}")

    if resolved_rhwp is None:
        notes.append(f"rhwp 없음 — {rhwp_search_desc(rhwp_bin)}")
        return done([], "none", notes)
    if not any(e["top_pi"] is not None for e in eligible):
        notes.append("rhwp 미사용 — 본문 밖(각주) 문단은 rhwp가 문단별로 보고하지 않음")
        return done([], "none", notes)
    rhwp_cands = _detect_with_rhwp(
        src, header_xml, section_blobs, eligible, resolved_rhwp,
        max_chars, min_spacing, min_ratio,
    )
    if rhwp_cands is None:
        notes.append(f"rhwp 실행 실패 ({resolved_rhwp} dump-pages)")
        return done([], "none", notes)
    # footnote paragraphs are not reported per paragraph by rhwp: saved layout only
    for e in eligible:
        if e["top_pi"] is None:
            hangul = _hangul_tail(e, max_chars)
            if hangul:
                rhwp_cands.append(_heuristic_candidate(e, *hangul, min_spacing, min_ratio))
    return done(rhwp_cands, "rhwp", notes)


def detect_tail_fits(
    src: str | Path,
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
    include_footnotes: bool = False,
    rhwp_bin: str | None = None,
    hwp2pdf: Hwp2PdfClient | None = None,
    use_hwp2pdf: bool = True,
) -> list[TailFitCandidate]:
    """Candidates only — see :func:`detect_tail_fits_with_engine`.

    An empty list is ambiguous here (no tails *or* no engine); callers that
    report to a user should use :func:`detect_tail_fits_with_engine`.
    """
    return detect_tail_fits_with_engine(
        src, max_chars, min_spacing, min_ratio, include_footnotes,
        rhwp_bin, hwp2pdf, use_hwp2pdf,
    ).candidates


def format_no_engine_report(detection: TailFitDetection) -> str:
    """The "could not judge" message — must never read like "no tails"."""
    lines = ["꼬리 줄 압축: 판정 엔진 없음 — 적용하지 않음 (대상 없음이 아님)"]
    lines += [f"  - {n}" for n in detection.notes]
    return "\n".join(lines)


def format_tail_fit_report(
    candidates: list[TailFitCandidate],
    max_chars: int = 4,
    min_spacing: int = -5,
    min_ratio: int = 95,
    engine_label: str | None = None,
    partial: bool = False,
) -> str:
    """Format human-readable dry-run / audit report with before/after line counts.

    ``engine_label`` (:attr:`TailFitDetection.engine_label`) names the layout the
    candidates were judged on; it is printed so "대상 없음" always says on what.
    ``partial`` (:attr:`TailFitDetection.partial`) marks that some paragraphs
    went unjudged, so an empty result never reads as "no tails".
    """
    basis = f" [판정: {engine_label}]" if engine_label else ""
    if not candidates:
        if partial:
            return (
                "꼬리 줄 압축(widow-tail fitting): 일부만 판정 — "
                f"판정한 문단에는 대상 없음 (대상 없음이 아님).{basis}"
            )
        return f"꼬리 줄 압축(widow-tail fitting) 대상 문단 없음.{basis}"

    fittable_count = sum(1 for c in candidates if c.fittable)
    mismatch_count = sum(1 for c in candidates if c.skip_reason == "layout_mismatch")
    unfittable_count = len(candidates) - fittable_count - mismatch_count
    total_diff = sum(c.target_lines - c.total_lines for c in candidates if c.fittable)

    summary = (
        f"꼬리 줄 압축(widow-tail fitting) 대상 문단: 총 {len(candidates)}건 "
        f"(적용 가능: {fittable_count}건, 한도 초과: {unfittable_count}건"
    )
    if mismatch_count:
        summary += f", 검증 불가: {mismatch_count}건"
    summary += ")"
    if fittable_count:
        summary += f", 줄 수 합계 {total_diff}줄"
    if partial:
        summary += " — 일부만 판정"
    lines = [summary + basis]
    for idx, c in enumerate(candidates, start=1):
        diff = c.target_lines - c.total_lines
        diff_str = f" ({diff}줄)" if diff != 0 else ""
        if c.fittable:
            status = (
                f"총 {c.total_lines}줄 -> {c.target_lines}줄{diff_str}, "
                f"자간 {c.target_spacing}%, 장평 {c.target_ratio}% (적용 가능)"
            )
        elif c.skip_reason == "layout_mismatch":
            status = (
                f"총 {c.total_lines}줄 -> 검증 불가 "
                "(rhwp 조판이 한글 저장 조판과 달라 효과를 잴 수 없음, 미적용)"
            )
        else:
            status = (
                f"총 {c.total_lines}줄 -> 한도 초과 "
                f"(자간 {min_spacing}%, 장평 {min_ratio}%로도 줄 수 유지, 미적용)"
            )
        page_info = f" (쪽 {c.page_num})" if c.page_num is not None else ""
        engine_tag = " [rhwp 판정]" if c.engine == "rhwp" else ""
        lines.append(
            f"  [{idx}] {c.section} #{c.para_index}{page_info}{engine_tag}: "
            f"\"{c.text_preview}\""
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
