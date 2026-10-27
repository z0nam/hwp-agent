"""Page filling (issue #40).

When a page ends with a large empty band, or only the last few lines of a page
spill onto the next one, tighten the paragraphs of that page — paragraph
spacing first, then line spacing, uniformly across the page and within limits —
so the short chunk at the top of the next page moves back. If it cannot fit,
nothing is changed and the page is reported ("여백 N% — 수동 판단 필요").

Layout engines follow #61/#65 (shared with ``fit tails``): the Hangul-saved
linesegarray when every paragraph has one, else a Hangul re-save through
hwp2pdf, else rhwp, else nothing is judged (``engine == "none"``). A hwp2pdf
re-save can lay out only the first pages (#65); pages it left without a layout
are judged by rhwp, and every page reports which engine judged it — engines are
never mixed inside one page.

How a Hangul linesegarray encodes pages (measured on a 21-page institute report,
2026-10-27, against Hangul's own PDF): ``vertpos`` restarts near 0 at the top of
every page (it includes the paragraph's spacing-before), so a page break is a
line whose ``vertpos`` is lower than the previous line's. The usable height is
``page height − top − bottom − header − footer`` margins, and footnotes stack
upward from the bottom of it, each footnote line taking ``vertsize + spacing``.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import etree

from ..render.remote_hwp2pdf import Hwp2PdfClient, Hwp2PdfError
from ..render.rhwp import resolve_rhwp, rhwp_search_desc
from .container import _rewrite_zip_preserving
from .fit_tails import (
    _XML_DECL,
    ENGINE_LABELS,
    _heading_identifiers,
    _hh,
    _hp,
    _para_text,
    _section_parts,
    hwp2pdf_resave_sections,
    usable_hwp2pdf,
)

_HC = "http://www.hancom.co.kr/hwpml/2011/core"

#: rhwp reports pixels at 96 dpi; 1 px = 75 HWPUNIT.
_PX = 75.0

#: Extra room the plan keeps on top of the computed need (≈1 pt).
_MARGIN = 100

#: Objects that make a paragraph a block (its line is the object's height).
_OBJECT_TAGS = {_hp(t) for t in ("tbl", "pic", "container", "ole", "rect", "ellipse",
                                 "polygon", "curve", "arc", "line", "chart", "video")}


# --------------------------------------------------------------------------- model


@dataclass
class _ParaInfo:
    """What the plan needs from one top-level paragraph of the original."""

    kind: str  # "text" | "empty" | "object"
    text: str
    para_pr: str
    sb: int  # spacing before (HWPUNIT)
    sa: int  # spacing after
    percent: bool  # line spacing is PERCENT (the only kind line cuts apply to)
    heading: bool  # outline heading or keep-with-next: must not end a page alone
    forced: bool  # starts a page on purpose (pageBreak / pageBreakBefore)
    notes: list  # footNote elements anchored in this paragraph


@dataclass
class _Block:
    """One paragraph's piece on one page."""

    pi: int
    start: int  # index of the piece's first line within the paragraph
    lines: list[tuple[int, int]]  # (height, spacing after the line), HWPUNIT
    first: bool  # holds the paragraph's first line
    last: bool  # holds the paragraph's last line
    notes: list[int] = field(default_factory=list)  # its footnotes' heights (Hangul only)


@dataclass
class _Page:
    number: int  # physical page number (Hangul: counted; rhwp: its own)
    section: int
    engine: str  # "document" | "hwp2pdf" | "rhwp"
    blocks: list[_Block]
    bottom: int  # bottom of the last line, from the top of the body area
    body: int  # usable body height
    notes: int  # footnote area height on this page
    forced: bool  # this page starts with an intended break (or a section)
    partial: bool = False  # Hangul stopped laying out inside this page (#65)


@dataclass
class _Layout:
    pages: list[_Page]
    notes: list[str] = field(default_factory=list)

    def locate(self, section: int, pi: int, line: int) -> int | None:
        """Index into ``pages`` of the page holding that line, if laid out."""
        for i, pg in enumerate(self.pages):
            if pg.section != section:
                continue
            for b in pg.blocks:
                if b.pi == pi and b.start <= line < b.start + len(b.lines):
                    return i
        return None


@dataclass
class PageFit:
    """One page boundary the fitter looked at (issue #40)."""

    page: int  # physical page number of the page that would take the chunk
    section: str
    engine: str  # engine that judged this page
    kind: str  # "pull" (짧은 덩어리) | "gap" (여백 과다) | "forced" (의도된 쪽 나눔)
    body: int  # usable body height (HWPUNIT)
    gap_before: int  # empty band at the bottom, before (HWPUNIT)
    chunk: str  # preview of the chunk at the top of the next page
    chunk_lines: int
    needed: int  # height to win back for the chunk to fit (HWPUNIT)
    fittable: bool
    spacing_cut: int = 0  # % cut of paragraph spacing before/after
    line_cut: int = 0  # % cut of line spacing
    gap_after: int | None = None  # bottom band after (predicted, or measured if applied)
    # "planned" (dry-run) | "applied" | "unverified" | "reverted" | "unfittable" | "forced"
    status: str = "planned"
    paragraphs: list[int] = field(default_factory=list)  # adjusted top-level indices

    @property
    def gap_pct(self) -> float:
        return 100.0 * self.gap_before / self.body if self.body else 0.0

    @property
    def gap_after_pct(self) -> float | None:
        if self.gap_after is None or not self.body:
            return None
        return 100.0 * self.gap_after / self.body

    def as_dict(self) -> dict:
        d = asdict(self)
        d["gap_pct"] = round(self.gap_pct, 1)
        after = self.gap_after_pct
        d["gap_after_pct"] = None if after is None else round(after, 1)
        return d


@dataclass
class PageFitDetection:
    """Page boundaries plus the engines they were judged on.

    ``engine == "none"`` means nothing could be judged — not "nothing to do".
    ``pages`` counts judged pages per engine (e.g. ``{"hwp2pdf": 5, "rhwp": 12}``).
    """

    fits: list[PageFit]
    engine: str  # "document" | "hwp2pdf" | "rhwp" | "none"
    notes: list[str]
    pages: dict[str, int] = field(default_factory=dict)
    applied: int = 0

    @property
    def judged(self) -> bool:
        return self.engine != "none"

    @property
    def engine_label(self) -> str:
        label = ENGINE_LABELS[self.engine]
        if len([k for k, v in self.pages.items() if v]) > 1:
            split = " · ".join(
                f"{'한글' if k in ('hwp2pdf', 'document') else k} {v}쪽"
                for k, v in self.pages.items() if v
            )
            return f"{label} — {split}"
        return label


@dataclass
class _Doc:
    """The original document, read once."""

    path: Path
    header_xml: bytes
    sections: dict[str, bytes]  # section part name -> original XML
    paras: dict[str, list[_ParaInfo]]
    body: dict[str, int]
    note_gap: dict[str, int]  # footnote separator (above + below line)
    multi_column: set[str]

    def sidx(self, name: str) -> int:
        return list(self.sections).index(name)


# --------------------------------------------------------------------------- reading


def _int(v: str | None, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def _para_props(header: etree._Element) -> dict[str, tuple[int, int, bool, bool, bool]]:
    """paraPr id -> (spacing before, after, PERCENT, keepWithNext, pageBreakBefore)."""
    out: dict[str, tuple[int, int, bool, bool, bool]] = {}
    for pp in header.iter(_hh("paraPr")):
        # hp:switch puts the HWPUNIT values (hp:case) first; plain files have one margin
        margin = pp.find(".//" + _hh("margin"))
        sb = sa = 0
        if margin is not None:
            prev = margin.find(f"{{{_HC}}}prev")
            nxt = margin.find(f"{{{_HC}}}next")
            sb = _int(prev.get("value")) if prev is not None else 0
            sa = _int(nxt.get("value")) if nxt is not None else 0
        ls = pp.find(".//" + _hh("lineSpacing"))
        percent = ls is None or ls.get("type", "PERCENT") == "PERCENT"
        bs = pp.find(_hh("breakSetting"))
        keep = bs is not None and bs.get("keepWithNext") == "1"
        before = bs is not None and bs.get("pageBreakBefore") == "1"
        out[pp.get("id", "")] = (sb, sa, percent, keep, before)
    return out


def _para_kind(p: etree._Element, text: str) -> str:
    for el in p.iter():
        if el.tag in _OBJECT_TAGS:
            return "object"
    return "text" if text.strip() else "empty"


def _section_geometry(root: etree._Element) -> tuple[int, int, bool]:
    """(usable body height, footnote separator height, multi-column) of a section."""
    sec = root.find(".//" + _hp("secPr"))
    if sec is None:
        return 0, 0, False
    pp = sec.find(_hp("pagePr"))
    body = 0
    if pp is not None:
        h, w = _int(pp.get("height")), _int(pp.get("width"))
        height = w if pp.get("landscape") == "NARROWLY" else h
        m = pp.find(_hp("margin"))
        if m is not None:
            body = height - sum(_int(m.get(k)) for k in ("top", "bottom", "header", "footer"))
    sep = 0
    ns = sec.find(f"{_hp('footNotePr')}/{_hp('noteSpacing')}")
    if ns is not None:
        sep = _int(ns.get("aboveLine")) + _int(ns.get("belowLine"))
    col = root.find(".//" + _hp("colPr"))
    multi = col is not None and _int(col.get("colCount"), 1) > 1
    return body, sep, multi


def _read_doc(src: Path) -> _Doc:
    with zipfile.ZipFile(str(src)) as z:
        names = z.namelist()
        header_xml = z.read("Contents/header.xml") if "Contents/header.xml" in names else b""
        sections = {s: z.read(s) for s in _section_parts(names)}
    header = etree.fromstring(header_xml) if header_xml else etree.Element("x")
    props = _para_props(header)
    heading_styles, heading_pps = _heading_identifiers(header_xml.decode("utf-8"))
    paras: dict[str, list[_ParaInfo]] = {}
    body: dict[str, int] = {}
    note_gap: dict[str, int] = {}
    multi: set[str] = set()
    for name, blob in sections.items():
        root = etree.fromstring(blob)
        body[name], note_gap[name], is_multi = _section_geometry(root)
        if is_multi:
            multi.add(name)
        infos: list[_ParaInfo] = []
        for p in root.findall(_hp("p")):
            pp_id = p.get("paraPrIDRef") or "0"
            sb, sa, percent, keep, before = props.get(pp_id, (0, 0, True, False, False))
            text = _para_text(p)
            infos.append(_ParaInfo(
                kind=_para_kind(p, text),
                text=text,
                para_pr=pp_id,
                sb=sb,
                sa=sa,
                percent=percent,
                heading=keep or pp_id in heading_pps or p.get("styleIDRef") in heading_styles,
                forced=p.get("pageBreak") == "1" or before,
                notes=list(p.iter(_hp("footNote"))),
            ))
        paras[name] = infos
    return _Doc(src, header_xml, sections, paras, body, note_gap, multi)


# --------------------------------------------------------------------------- engines


def _segs(p: etree._Element) -> list[tuple[int, int, int]]:
    return [
        (_int(g.get("vertpos")), _int(g.get("vertsize")), _int(g.get("spacing")))
        for g in p.findall(f"{_hp('linesegarray')}/{_hp('lineseg')}")
    ]


def _note_height(fn: etree._Element) -> int | None:
    lines = [s for p in fn.iter(_hp("p")) for s in _segs(p)]
    if not lines:
        return None
    return sum(h + sp for _, h, sp in lines) - lines[-1][2]


def _hangul_pages(
    doc: _Doc, blobs: dict[str, bytes], engine: str
) -> tuple[list[_Page], dict[str, int]]:
    """Pages read from Hangul linesegarrays.

    Returns the complete pages and, per section, the first top-level paragraph
    *not* covered (the rest of that section is left to rhwp; absent = all covered).
    A page is complete once the next page has started; a page cut short by a
    paragraph without a layout (#65) is dropped and handed to rhwp.
    """
    pages: list[_Page] = []
    uncovered: dict[str, int] = {}
    number = 0
    for name in doc.sections:
        if name not in blobs or name in doc.multi_column:
            uncovered[name] = 0
            continue
        tops = etree.fromstring(blobs[name]).findall(_hp("p"))
        infos = doc.paras[name]
        if len(tops) != len(infos):  # not the same document structure
            uncovered[name] = 0
            continue
        s_idx = doc.sidx(name)
        sec_pages: list[_Page] = []
        cur: _Page | None = None
        prev_vp = None
        stop: int | None = None
        for pi, p in enumerate(tops):
            segs = _segs(p)
            if not segs:
                stop = pi
                break
            for li, (vp, h, sp) in enumerate(segs):
                # an intended break starts a page even when vertpos does not drop
                # (a one-line page followed by the same spacing-before)
                if (cur is None or (prev_vp is not None and vp < prev_vp)
                        or (li == 0 and infos[pi].forced and cur.blocks)):
                    number += 1
                    cur = _Page(number, s_idx, engine, [], 0, doc.body[name], 0,
                                forced=cur is None or (li == 0 and infos[pi].forced))
                    sec_pages.append(cur)
                if not cur.blocks or cur.blocks[-1].pi != pi:
                    heights = [nh for fn in p.iter(_hp("footNote"))
                               if (nh := _note_height(fn)) is not None] if li == 0 else []
                    cur.blocks.append(_Block(pi, li, [], li == 0, False, heights))
                blk = cur.blocks[-1]
                blk.lines.append((h, sp))
                blk.last = li == len(segs) - 1
                cur.bottom = vp + h
                prev_vp = vp
        if stop is not None:
            # the page in progress is incomplete: kept only so the page before it
            # can see its first chunk; rhwp judges from its first paragraph on
            if sec_pages:
                last = sec_pages[-1]
                last.partial = True
                stop = last.blocks[0].pi if last.blocks[0].first else last.blocks[0].pi + 1
            uncovered[name] = stop
        for pg in sec_pages:
            heights = [h for b in pg.blocks for h in b.notes]
            if heights:
                pg.notes = doc.note_gap[name] + sum(heights)
        pages += sec_pages
    return pages, uncovered


_R_PAGE = re.compile(r"=== 페이지 (\d+) \(global_idx=\d+, section=(\d+), page_num=\d+\)")
_R_BODY = re.compile(r"body_area: .*?\bh=([\d.]+)")
_R_COL = re.compile(r"단 (\d+) \(items=\d+, used=([\d.]+)px")
_R_ITEM = re.compile(r"^\s+(FullParagraph|PartialParagraph|Table|Shape)\s+pi=(\d+)(.*)$")
_R_LINES = re.compile(r"\blines=(\d+)\.\.(\d+)")
_R_LS = re.compile(r"\bls=(\d+)\[(?:first )?ts=\d+ lh=(\d+) th=\d+ bl=\d+ gap=(-?\d+)")
_R_TBL = re.compile(r"\b[\d.]+x([\d.]+)px\s+wrap=\S+\s+tac=(true|false)")


def _parse_rhwp_pages(stdout: str, doc: _Doc) -> list[_Page]:
    names = list(doc.sections)
    pages: list[_Page] = []
    cur: _Page | None = None
    skip = False
    for line in stdout.splitlines():
        m = _R_PAGE.search(line)
        if m:
            sec = int(m.group(2))
            name = names[sec] if sec < len(names) else ""
            cur = _Page(int(m.group(1)), sec, "rhwp", [], 0, doc.body.get(name, 0), 0, False)
            skip = name in doc.multi_column
            if not skip:
                pages.append(cur)
            continue
        if cur is None or skip:
            continue
        if m := _R_BODY.search(line):
            cur.body = cur.body or round(float(m.group(1)) * _PX)
            continue
        if m := _R_COL.search(line):
            if m.group(1) != "0":  # multi-column page: not handled
                pages.remove(cur)
                skip = True
            else:
                cur.bottom = round(float(m.group(2)) * _PX)
            continue
        m = _R_ITEM.match(line)
        if not m:
            continue
        kind, pi, rest = m.group(1), int(m.group(2)), m.group(3)
        ls = _R_LS.search(rest)
        if kind in ("FullParagraph", "PartialParagraph") and ls:
            n, lh, gap = int(ls.group(1)), int(ls.group(2)), int(ls.group(3))
            lm = _R_LINES.search(rest) if kind == "PartialParagraph" else None
            a, b = (int(lm.group(1)), int(lm.group(2))) if lm else (0, n)
            cur.blocks.append(_Block(pi, a, [(lh, gap)] * (b - a), a == 0, b >= n))
        elif kind == "Table" and (t := _R_TBL.search(rest)) and t.group(2) == "true":
            gap = int(ls.group(3)) if ls else 0
            if cur.blocks and cur.blocks[-1].pi == pi:
                continue
            cur.blocks.append(_Block(pi, 0, [(round(float(t.group(1)) * _PX), gap)], True, True))
    for pg in pages:
        infos = doc.paras[names[pg.section]]
        tail = pg.blocks[-1] if pg.blocks else None
        if tail is not None and tail.last and tail.pi < len(infos):
            pg.bottom -= infos[tail.pi].sa  # rhwp's "used" includes the last spacing-after
        pg.forced = not pg.blocks or (
            pg.blocks[0].first and (
                pg.blocks[0].pi == 0
                or doc.paras[names[pg.section]][pg.blocks[0].pi].forced
            )
        )
    return [pg for pg in pages if pg.blocks]


def _rhwp_note_areas(path: Path, rhwp_bin: str) -> dict[int, int]:
    """Footnote area height per page (1-based) as rhwp lays it out."""
    out: dict[int, int] = {}
    with tempfile.TemporaryDirectory() as td:
        res = subprocess.run(  # noqa: S603
            [rhwp_bin, "export-render-tree", str(path), "-o", td],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            return out
        for f in Path(td).glob("render_tree_*.json"):
            m = re.search(r"(\d+)\.json$", f.name)
            try:
                tree = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for node in tree.get("children", []):
                if node.get("type") == "FootnoteArea" and m:
                    out[int(m.group(1))] = round(float(node["bbox"]["h"]) * _PX)
    return out


def _strip_all_linesegs(path: Path, dst: Path) -> None:
    overrides: dict[str, bytes] = {}
    with zipfile.ZipFile(str(path)) as z:
        for name in _section_parts(z.namelist()):
            root = etree.fromstring(z.read(name))
            for lsa in list(root.iter(_hp("linesegarray"))):
                lsa.getparent().remove(lsa)
            overrides[name] = _XML_DECL + etree.tostring(root, encoding="UTF-8")
    _rewrite_zip_preserving(path, dst, overrides)


def _rhwp_pages(path: Path, doc: _Doc, rhwp_bin: str) -> list[_Page] | None:
    """rhwp's own pagination (every linesegarray removed so rhwp lays out all)."""
    with tempfile.TemporaryDirectory() as td:
        bare = Path(td) / "bare.hwpx"
        _strip_all_linesegs(path, bare)
        res = subprocess.run(  # noqa: S603
            [rhwp_bin, "dump-pages", str(bare)], capture_output=True, text=True
        )
        if res.returncode != 0:
            return None
        pages = _parse_rhwp_pages(res.stdout, doc)
        if any(doc.paras[n][pi].notes for n in doc.sections for pi in range(len(doc.paras[n]))):
            areas = _rhwp_note_areas(bare, rhwp_bin)
            for pg in pages:
                pg.notes = areas.get(pg.number, 0)
    return pages


class _Measurer:
    """Lays out a variant of the document with the engine chosen at detection."""

    def __init__(self, doc: _Doc, mode: str, client: Hwp2PdfClient | None,
                 rhwp_bin: str | None, rhwp_arg: str | None) -> None:
        self.doc = doc
        self.mode = mode  # "document" | "hwp2pdf" | "rhwp"
        self.client = client
        self.rhwp_bin = rhwp_bin
        self.rhwp_arg = rhwp_arg

    def measure(self, path: Path, first: bool = False) -> _Layout | None:
        """None when the engine fails (the caller stops; nothing is guessed)."""
        doc = self.doc
        if self.mode == "rhwp":
            pages = _rhwp_pages(path, doc, self.rhwp_bin) if self.rhwp_bin else None
            return None if pages is None else _Layout(pages)
        notes: list[str] = []
        if self.mode == "document" and first:
            blobs, engine = doc.sections, "document"
        elif self.client is not None:
            try:
                blobs = hwp2pdf_resave_sections(self.client, path, list(doc.sections))
            except Hwp2PdfError as exc:
                notes.append(f"hwp2pdf 판정 실패 — {exc}")
                return _Layout([], notes) if first else None
            engine = "hwp2pdf"
        else:
            return None
        pages, uncovered = _hangul_pages(doc, blobs, engine)
        if uncovered:
            rest = self._rhwp_rest(path, uncovered, notes)
            pages += rest
        return _Layout(pages, notes)

    def _rhwp_rest(self, path: Path, uncovered: dict[str, int], notes: list[str]) -> list[_Page]:
        names = list(self.doc.sections)
        desc = ", ".join(
            f"{n.rsplit('/', 1)[-1]} 문단 {pi}부터" for n, pi in uncovered.items()
        )
        lead = f"한글 줄 배치가 없는 쪽({desc})"
        if self.rhwp_bin is None:
            notes.append(f"{lead}은 판정 못함 — rhwp 없음 — {rhwp_search_desc(self.rhwp_arg)}")
            return []
        try:
            pages = _rhwp_pages(path, self.doc, self.rhwp_bin)
        except OSError as exc:
            pages = None
            notes.append(f"{lead}은 판정 못함 — rhwp 실행 실패 ({exc.strerror or exc})")
            return []
        if pages is None:
            notes.append(f"{lead}은 판정 못함 — rhwp 실행 실패 ({self.rhwp_bin} dump-pages)")
            return []
        out = []
        for pg in pages:
            name = names[pg.section]
            if name in uncovered and pg.blocks[0].pi >= uncovered[name]:
                out.append(pg)
        notes.append(f"{lead}은 rhwp로 판정 (한글이 앞쪽만 조판해 저장함, #65)")
        return out


# --------------------------------------------------------------------------- planning


def _ladder(max_spacing_cut: int, max_line_cut: int) -> list[tuple[int, int]]:
    """(spacing cut %, line cut %) steps: paragraph spacing first, then line spacing."""
    steps = [(s, 0) for s in range(10, max_spacing_cut + 1, 10)]
    if max_spacing_cut % 10:
        steps.append((max_spacing_cut, 0))
    steps += [(max_spacing_cut, c) for c in range(2, max_line_cut + 1, 2)]
    if max_line_cut % 2:
        steps.append((max_spacing_cut, max_line_cut))
    return steps


def _chunk(next_page: _Page, infos: list[_ParaInfo]) -> list[_Block] | None:
    """The leading chunk of *next_page* that would move back, or None.

    A continued paragraph's remaining lines; else the first block with any
    empty spacer paragraphs before it; a heading (or keep-with-next paragraph)
    takes the next block with it — only its first two lines when that paragraph
    is long enough to split. Only whole paragraphs move otherwise.
    """
    blocks = next_page.blocks
    if not blocks[0].first:
        return [blocks[0]] if blocks[0].last else None
    out: list[_Block] = []
    i = 0
    while i < len(blocks) and infos[blocks[i].pi].kind == "empty":
        out.append(blocks[i])
        i += 1
    if i >= len(blocks) or not blocks[i].last:
        return None
    out.append(blocks[i])
    if infos[blocks[i].pi].heading:
        if i + 1 >= len(blocks):
            return None
        nxt = blocks[i + 1]
        if not nxt.first:
            return None
        if infos[nxt.pi].kind == "text" and len(nxt.lines) >= 4:
            out.append(_Block(nxt.pi, nxt.start, nxt.lines[:2], True, False, nxt.notes))
        elif nxt.last:
            out.append(nxt)
        else:
            return None
    return out


def _preview(chunk: list[_Block], infos: list[_ParaInfo]) -> str:
    for b in chunk:
        info = infos[b.pi]
        if info.kind == "object":
            return "[표·개체]"
        if info.kind == "text":
            lead = "…" if not b.first else ""
            return lead + info.text[:30].replace("\n", " ")
    return "(빈 문단)"


def _plan_boundary(
    page: _Page,
    nxt: _Page,
    infos: list[_ParaInfo],
    section: str,
    note_gap: int,
    gap_threshold: float,
    max_lines: int,
    ladder: list[tuple[int, int]],
) -> PageFit | None:
    gap = page.body - page.notes - page.bottom
    gap_pct = 100.0 * gap / page.body if page.body else 0.0

    def fit(kind: str, **kw) -> PageFit:
        return PageFit(page.number, section, page.engine, kind, page.body, gap, **kw)

    if nxt.forced:
        if gap_pct < gap_threshold:
            return None
        return fit("forced", chunk="", chunk_lines=0, needed=0, fittable=False,
                   status="forced")
    chunk = _chunk(nxt, infos)
    if chunk is None:
        if gap_pct < gap_threshold:
            return None
        return fit("gap", chunk=_preview(nxt.blocks[:1], infos), chunk_lines=0, needed=0,
                   fittable=False, status="unfittable")
    lines = sum(len(b.lines) for b in chunk if infos[b.pi].kind != "empty")
    small = lines <= max_lines and all(infos[b.pi].kind != "object" for b in chunk)
    kind = "gap" if gap_pct >= gap_threshold else "pull"
    if kind == "pull" and not small:
        return None

    # the flow continued from the bottom of the page through the chunk
    tail = page.blocks[-1]
    add = tail.lines[-1][1] + (infos[tail.pi].sa if tail.last else 0)
    for j, b in enumerate(chunk):
        info = infos[b.pi]
        add += (info.sb if b.first else 0) + sum(h + sp for h, sp in b.lines)
        if j < len(chunk) - 1 and b.last:
            add += info.sa
    add -= chunk[-1].lines[-1][1]
    notes = page.notes
    if page.engine != "rhwp":  # rhwp's footnote area is its own; it is not re-derived
        extra = [h for b in chunk for h in b.notes]
        if extra:
            notes = (notes or note_gap) + sum(extra)
    needed = page.bottom + add + notes - page.body

    # what the page (and the chunk) can give back
    adjusted = [b for b in page.blocks if b.first] + chunk
    para_total = 0
    line_total = 0
    for j, b in enumerate(adjusted):
        info = infos[b.pi]
        para_total += info.sb if b.first else 0
        if b.last and j < len(adjusted) - 1:
            para_total += info.sa
        if info.percent and info.kind != "object":
            line_total += sum(h + sp for h, sp in b.lines)
    if infos[chunk[-1].pi].percent and infos[chunk[-1].pi].kind != "object":
        line_total -= sum(chunk[-1].lines[-1])

    pis = sorted({b.pi for b in adjusted})
    need = max(needed, 0) + _MARGIN
    for sp_cut, ln_cut in ladder:
        gain = (para_total * sp_cut + line_total * ln_cut) // 100
        if gain >= need:
            return fit(kind, chunk=_preview(chunk, infos), chunk_lines=lines, needed=needed,
                       fittable=True, spacing_cut=sp_cut, line_cut=ln_cut,
                       gap_after=gain - needed, paragraphs=pis)
    return fit(kind, chunk=_preview(chunk, infos), chunk_lines=lines, needed=needed,
               fittable=False, status="unfittable")


@dataclass
class _Plan:
    fit: PageFit
    key: tuple[int, int, int]  # (section, chunk paragraph, its first line)
    page_first: tuple[int, int, int]  # first line of the page that takes the chunk
    anchor: tuple[int, int, int]  # last line on the page that takes the chunk
    chunk_end: tuple[int, int, int]
    segment: tuple[int, int]  # (section, first paragraph of the run of pages)


def _plans(layout: _Layout, doc: _Doc, gap_threshold: float, max_lines: int,
           ladder: list[tuple[int, int]]) -> list[_Plan]:
    names = list(doc.sections)
    out: list[_Plan] = []
    seg = (0, 0)
    pages = layout.pages
    for i, pg in enumerate(pages):
        if pg.forced:
            seg = (pg.section, pg.blocks[0].pi)
        if i + 1 >= len(pages):
            break
        if pg.partial:
            continue
        nxt = pages[i + 1]
        if nxt.section != pg.section or nxt.engine != pg.engine:
            continue  # a section starts a page; engines are never mixed on a boundary
        name = names[pg.section]
        infos = doc.paras[name]
        f = _plan_boundary(pg, nxt, infos, name, doc.note_gap[name], gap_threshold,
                           max_lines, ladder)
        if f is None:
            continue
        head = nxt.blocks[0]
        chunk = _chunk(nxt, infos) or [head]
        tail = pg.blocks[-1]
        last = chunk[-1]
        out.append(_Plan(
            f,
            key=(pg.section, head.pi, head.start),
            page_first=(pg.section, pg.blocks[0].pi, pg.blocks[0].start),
            anchor=(pg.section, tail.pi, tail.start + len(tail.lines) - 1),
            chunk_end=(pg.section, last.pi, last.start + len(last.lines) - 1),
            segment=seg,
        ))
    return out


# --------------------------------------------------------------------------- applying


def _scaled(v: str | None, cut: int) -> str:
    return str(round(_int(v) * (100 - cut) / 100))


def _clone_para_pr(pp: etree._Element, new_id: str, sp_cut: int, ln_cut: int) -> etree._Element:
    """Copy of *pp* with spacing before/after and PERCENT line spacing cut.

    Every variant inside hp:switch (HWPUNIT case and the default) is scaled the
    same way, so Hangul reads the same spacing whichever branch it takes.
    """
    clone = copy.deepcopy(pp)
    clone.set("id", new_id)
    for margin in clone.iter(_hh("margin")):
        for tag in ("prev", "next"):
            el = margin.find(f"{{{_HC}}}{tag}")
            if el is not None:
                el.set("value", _scaled(el.get("value"), sp_cut))
    for ls in clone.iter(_hh("lineSpacing")):
        if ls.get("type", "PERCENT") == "PERCENT":
            ls.set("value", _scaled(ls.get("value"), ln_cut))
    return clone


def _write_state(doc: _Doc, dst: Path, state: dict[tuple[str, int], tuple[int, int]]) -> None:
    """Write the original with *state* applied: paragraph -> (spacing cut, line cut).

    paraPr clones go at the end of paraProperties with itemCnt updated (#22);
    the original paraPr is never edited. Adjusted paragraphs lose their
    linesegarray so Hangul lays them out afresh (#15).
    """
    src = doc.path
    if not state:
        _rewrite_zip_preserving(src, dst, {})
        return
    header = etree.fromstring(doc.header_xml)
    props = header.find(".//" + _hh("paraProperties"))
    by_id = {pp.get("id"): pp for pp in props.findall(_hh("paraPr"))}
    next_id = max((_int(i, -1) for i in by_id), default=-1) + 1
    cache: dict[tuple[str, int, int], str] = {}
    overrides: dict[str, bytes] = {}
    for name, blob in doc.sections.items():
        mine = {pi: cut for (n, pi), cut in state.items() if n == name}
        if not mine:
            continue
        root = etree.fromstring(blob)
        for pi, p in enumerate(root.findall(_hp("p"))):
            if pi not in mine:
                continue
            orig = p.get("paraPrIDRef") or "0"
            key = (orig, *mine[pi])
            if key not in cache and orig in by_id:
                new_id = str(next_id)
                next_id += 1
                props.append(_clone_para_pr(by_id[orig], new_id, *mine[pi]))
                cache[key] = new_id
            if key in cache:
                p.set("paraPrIDRef", cache[key])
            lsa = p.find(_hp("linesegarray"))
            if lsa is not None:
                p.remove(lsa)
        overrides[name] = _XML_DECL + etree.tostring(root, encoding="UTF-8")
    props.set("itemCnt", str(len(props.findall(_hh("paraPr")))))
    overrides["Contents/header.xml"] = _XML_DECL + etree.tostring(header, encoding="UTF-8")
    _rewrite_zip_preserving(src, dst, overrides)


# --------------------------------------------------------------------------- driver


def _choose_mode(doc: _Doc, hwp2pdf: Hwp2PdfClient | None, use_hwp2pdf: bool,
                 rhwp_bin: str | None, rhwp_arg: str | None,
                 notes: list[str]) -> tuple[str, Hwp2PdfClient | None]:
    """"document" | "hwp2pdf" | "rhwp" | "none" — the #61 order.

    With a Hangul-saved layout on every paragraph that layout is judged
    directly; hwp2pdf (if usable) then only re-lays out to verify applied moves.
    """
    every = all(
        p.find(_hp("linesegarray")) is not None
        for blob in doc.sections.values()
        for p in etree.fromstring(blob).findall(_hp("p"))
    )
    client = usable_hwp2pdf(hwp2pdf, use_hwp2pdf, notes)
    if every:
        return "document", client
    if client is not None:
        return "hwp2pdf", client
    if rhwp_bin is not None:
        return "rhwp", None
    notes.append(f"rhwp 없음 — {rhwp_search_desc(rhwp_arg)}")
    return "none", None


def _counts(layout: _Layout) -> dict[str, int]:
    out: dict[str, int] = {}
    for pg in layout.pages:
        if pg.partial:
            continue
        out[pg.engine] = out.get(pg.engine, 0) + 1
    return out


def fit_pages(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    dry_run: bool = False,
    max_spacing_cut: int = 50,
    max_linespacing_cut: int = 10,
    gap_threshold: float = 25.0,
    max_lines: int = 3,
    rhwp_bin: str | None = None,
    hwp2pdf: Hwp2PdfClient | None = None,
    use_hwp2pdf: bool = True,
    max_rounds: int = 8,
) -> PageFitDetection:
    """Judge page boundaries and (unless *dry_run*) pull short chunks back.

    Dry run judges every boundary once on the current layout (each estimate
    assumes the pages before it are left as they are). Applying works in
    rounds: per run of pages between intended breaks the first fittable
    boundary is applied, the document is laid out again with the same engine
    and the move is verified — if the chunk did not come back the cut is raised
    to the limit once, and if it still does not the change is undone
    ("reverted"). Only *dst* is written; *src* is never modified.
    """
    src = Path(src)
    doc = _read_doc(src)
    notes: list[str] = []
    resolved = resolve_rhwp(rhwp_bin)
    mode, client = _choose_mode(doc, hwp2pdf, use_hwp2pdf, resolved, rhwp_bin, notes)
    if mode == "none":
        return PageFitDetection([], "none", notes)
    measurer = _Measurer(doc, mode, client, resolved, rhwp_bin)
    ladder = _ladder(max_spacing_cut, max_linespacing_cut)

    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "work.hwpx"
        _write_state(doc, work, {})
        layout = measurer.measure(work, first=True)
        if layout is not None:
            notes += layout.notes
        if (layout is None or not layout.pages) and mode == "hwp2pdf" and resolved:
            mode = "rhwp"  # the re-save failed outright: rhwp judges the whole document
            measurer = _Measurer(doc, mode, None, resolved, rhwp_bin)
            layout = measurer.measure(work, first=True)
        if layout is None or not layout.pages:
            if layout is None:
                notes.append("판정 엔진 실행 실패")
            if mode == "rhwp" and layout is not None:
                notes.append("rhwp가 쪽을 하나도 보고하지 않음")
            return PageFitDetection([], "none", notes)
        engine = mode
        counts = _counts(layout)
        if dry_run or dst is None:
            fits = [p.fit for p in _plans(layout, doc, gap_threshold, max_lines, ladder)]
            return PageFitDetection(fits, engine, notes, counts)

        state: dict[tuple[str, int], tuple[int, int]] = {}
        decided: dict[tuple[int, int, int], PageFit] = {}
        done_pages: set[tuple[int, int, int]] = set()  # each page is tightened once
        names = list(doc.sections)
        verifiable = mode != "document" or client is not None

        def remeasure() -> _Layout | None:
            _write_state(doc, work, state)
            return measurer.measure(work)

        def moved(lay: _Layout | None, pl: _Plan) -> bool | None:
            if lay is None:
                return None
            # the page keeps its first line and now also ends with the chunk
            where = {lay.locate(*k) for k in (pl.page_first, pl.anchor, pl.key, pl.chunk_end)}
            if None in where:
                return None
            return len(where) == 1

        for _ in range(max_rounds):
            chosen: list[_Plan] = []
            seen_segments: set[tuple[int, int]] = set()
            for pl in _plans(layout, doc, gap_threshold, max_lines, ladder):
                if (pl.key in decided or pl.page_first in done_pages
                        or pl.segment in seen_segments):
                    continue
                if not pl.fit.fittable:
                    decided[pl.key] = pl.fit
                    continue
                seen_segments.add(pl.segment)
                chosen.append(pl)
            if not chosen:
                break
            for pl in chosen:
                done_pages.add(pl.page_first)
                name = names[pl.key[0]]
                for pi in pl.fit.paragraphs:
                    state[(name, pi)] = (pl.fit.spacing_cut, pl.fit.line_cut)
            if not verifiable:
                for pl in chosen:
                    pl.fit.status = "unverified"
                    decided[pl.key] = pl.fit
                break
            new = remeasure()
            retry: list[_Plan] = []
            for pl in chosen:
                ok = moved(new, pl)
                if ok is False and (pl.fit.spacing_cut, pl.fit.line_cut) != ladder[-1]:
                    retry.append(pl)
                    continue
                _settle(pl, ok, new, state, names, decided)
            if retry:
                for pl in retry:
                    pl.fit.spacing_cut, pl.fit.line_cut = ladder[-1]
                    for pi in pl.fit.paragraphs:
                        state[(names[pl.key[0]], pi)] = ladder[-1]
                new = remeasure()
                for pl in retry:
                    _settle(pl, moved(new, pl), new, state, names, decided)
                if any(pl.fit.status == "reverted" for pl in retry):
                    new = remeasure()
            if new is None:
                notes.append("적용 후 다시 조판하지 못해 중단 — 적용한 쪽은 검증 못함")
                break
            layout = new
        final = Path(dst)
        tmp = final.with_suffix(final.suffix + ".pagefit.tmp")  # dst may be src
        _write_state(doc, tmp, state)
        tmp.replace(final)
        fits = sorted(decided.values(), key=lambda f: (f.section, f.page))
        applied = sum(1 for f in fits if f.status in ("applied", "unverified"))
        return PageFitDetection(fits, engine, notes, counts, applied)


def _settle(pl: _Plan, ok: bool | None, lay: _Layout | None,
            state: dict[tuple[str, int], tuple[int, int]], names: list[str],
            decided: dict[tuple[int, int, int], PageFit]) -> None:
    name = names[pl.key[0]]
    if ok is False:
        for pi in pl.fit.paragraphs:
            state.pop((name, pi), None)
        pl.fit.status = "reverted"
        pl.fit.gap_after = None
    else:
        pl.fit.status = "applied" if ok else "unverified"
        if ok and lay is not None:
            idx = lay.locate(*pl.anchor)
            if idx is not None:
                pg = lay.pages[idx]
                pl.fit.gap_after = pg.body - pg.notes - pg.bottom
    decided[pl.key] = pl.fit


# --------------------------------------------------------------------------- report

_STATUS = {
    "planned": "당겨오기 가능",
    "applied": "당겨옴",
    "unverified": "적용(다시 조판해 확인하지 못함)",
    "reverted": "적용해 봤으나 넘어오지 않아 되돌림",
    "unfittable": "한도 안에서는 들어가지 않음 — 변경 없음",
    "forced": "의도된 쪽 나눔 — 손대지 않음",
}


def format_no_engine_report(detection: PageFitDetection) -> str:
    """The "could not judge" message — must never read like "nothing to do"."""
    lines = ["쪽 채움: 판정 엔진 없음 — 적용하지 않음 (대상 없음이 아님)"]
    lines += [f"  - {n}" for n in detection.notes]
    return "\n".join(lines)


def format_page_fit_report(detection: PageFitDetection, dry_run: bool) -> str:
    basis = f" [판정: {detection.engine_label}]"
    fits = detection.fits
    if not fits:
        return f"쪽 채움(page fill) 대상 쪽 없음.{basis}"
    head = (
        f"쪽 채움(page fill): 살펴본 쪽 경계 {len(fits)}곳"
        + (f", 적용 {detection.applied}곳" if not dry_run else
           f", 당겨오기 가능 {sum(1 for f in fits if f.fittable)}곳")
        + basis
    )
    lines = [head]
    for f in fits:
        tag = {"pull": "짧은 덩어리", "gap": "여백 과다", "forced": "여백"}[f.kind]
        eng = "한글" if f.engine in ("hwp2pdf", "document") else "rhwp"
        lines.append(f"  쪽 {f.page} [{eng}] {tag}: 하단 여백 {max(f.gap_pct, 0):.0f}%"
                     + (f" — 다음 쪽 첫머리 \"{f.chunk}\" ({f.chunk_lines}줄)"
                        if f.kind != "forced" else ""))
        status = _STATUS[f.status]
        if f.status == "unfittable" and f.kind == "gap":
            status = f"여백 {max(f.gap_pct, 0):.0f}% — 수동 판단 필요 ({status})"
        detail = ""
        if f.fittable and f.status in ("planned", "applied", "unverified", "reverted"):
            detail = f" (문단 간격 −{f.spacing_cut}%, 줄간격 −{f.line_cut}%"
            after = f.gap_after_pct
            if after is not None and f.status != "reverted":
                word = "예상 " if f.status == "planned" else ""
                detail += f", {word}하단 여백 {max(after, 0):.0f}%"
            detail += f", 문단 {len(f.paragraphs)}개)"
        lines.append(f"      -> {status}{detail}")
    return "\n".join(lines)
