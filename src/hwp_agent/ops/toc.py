"""Fill a report template's 목차 section — 차례·표 차례·그림 차례 (issue #33).

The JI house forms ship their 목차 section as **static sample text**: a previous
report's chapter list (``Ⅰ. 세계경제 전망 ⇥ 2``…), then ``표 차례`` / ``그림 차례``
headers over that report's captions. ``build`` left all of it in place, so every
built report carried someone else's table of contents.

:func:`fill_toc` rewrites those lists from the built document itself:

* **차례** — one entry per HEADING_1‥n paragraph (as many levels as the template's
  sample shows), each a Hangul in-document hyperlink to the heading
  (``HWPHYPERLINK_TARGET_OUTLINE``), the whole list wrapped in Hangul's own
  ``TABLEOFCONTENTS`` field;
* **표 차례 / 그림 차례** — one entry per captioned table / figure (``<hp:caption>``
  on a ``numberingType`` TABLE / PICTURE object), linked to it
  (``HWPHYPERLINK_TARGET_TABLE`` / ``…_FIGURE``).

Page numbers are **not** computed: they exist only after Hangul lays the document
out. Each entry ends with the template's right-aligned tab and an empty page slot
for Hangul to fill (도구 › 차례/색인 › 차례 새로 고침).

The markup copies what Hangul itself writes — measured on a report whose 차례 was
made in Hangul and saved as HWPX (see ``docs/toc.md``). Entries reuse the
template's own sample entry paragraphs (paraPr/charPr/tab), so the look is the
form's; the sample's separator paragraphs between chapters (a blank + a rule line)
are repeated between generated chapters. Entries that are not numbered sample
headings (``요약``·``참고문헌``·``부록``) stay, with their sample page numbers
cleared.
"""

from __future__ import annotations

import copy
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .container import _rewrite_zip_preserving
from .links import HYPERLINK_FIELD_ID, new_field_id

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

#: Hangul's ``fieldid`` for a 차례 field and the ``Command`` it writes for
#: 차례 만들기 (개요 문단, 하이퍼링크) — verbatim from a Hangul-saved report.
TOC_FIELD_ID = "628387683"
TOC_COMMAND = (
    "TableOfContents:set:140:ContentsMake:uint:17 ContentsStyles:wstring:0: "
    "ContentsLevel:int:1 ContentsAutoTabRight:int:0 ContentsLeader:int:0 "
    "ContentsHyperlink:bool:1  "
)

#: hyperlink target kinds, as Hangul writes them in ``?#<id>;0;<kind>;0;``
_TARGETS = {
    "outline": (1, "HWPHYPERLINK_TARGET_OUTLINE"),
    "table": (2, "HWPHYPERLINK_TARGET_TABLE"),
    "figure": (3, "HWPHYPERLINK_TARGET_FIGURE"),
}

#: sample entry levels in a template's 차례: ``Ⅰ.`` / ``1.`` / ``1)``
_LEVEL_RES = (
    re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|[IVX]+)\.\s"),
    re.compile(r"^\s*\d+\.\s"),
    re.compile(r"^\s*\d+\)\s"),
)
_LIST_HEADER_RE = re.compile(r"^\s*(표|그림)\s*차\s*례\s*$")
_ARABIC_PAGE_RE = re.compile(r"^\s*\d+\s*$")
#: paragraph ids Hangul treats as "none" (can't be a hyperlink target)
_NO_ID = {"", "0", "2147483648"}


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


@dataclass
class TocEntry:
    level: int  # 1-based
    text: str
    target_id: str


@dataclass
class TocResult:
    section: str | None = None
    headings: list[TocEntry] = field(default_factory=list)
    tables: list[TocEntry] = field(default_factory=list)
    figures: list[TocEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        return self.section is not None


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _text(el, *, autonum: bool = False) -> str:
    """Visible text of a paragraph's own runs (tabs as ``\\t``).

    With *autonum*, an ``<hp:autoNum>`` counts as its ``num`` (caption numbers)."""
    out: list[str] = []
    for run in el.findall(_hp("run")):
        for child in run:
            if child.tag == _hp("t"):
                out.append(child.text or "")
                for sub in child:
                    out.append("\t" if sub.tag == _hp("tab") else "")
                    out.append(sub.tail or "")
            elif autonum and child.tag == _hp("ctrl"):
                for an in child.iter(_hp("autoNum")):
                    out.append(an.get("num", ""))
    return "".join(out)


def _label(p) -> str:
    """An entry's label — its text before the page-number tab."""
    return _text(p).split("\t", 1)[0].strip()


def _is_entry(p) -> bool:
    return any(t.get("type") == "2" for t in p.iter(_hp("tab")))


def _sample_level(p) -> int | None:
    label = _label(p)
    for i, rx in enumerate(_LEVEL_RES):
        if rx.match(label):
            return i + 1
    return None


def _section_parts(names) -> list[str]:
    parts = [n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)]
    return sorted(parts, key=lambda n: int(re.search(r"(\d+)", n).group(1)))


def find_toc_section(sections: dict[str, etree._Element]) -> str | None:
    """The part holding the template's 목차: numbered sample entries, or a
    ``표 차례`` / ``그림 차례`` header."""
    for name, root in sections.items():
        paras = root.findall(_hp("p"))
        if any(_LIST_HEADER_RE.match(_text(p)) for p in paras):
            return name
        if sum(1 for p in paras if _is_entry(p) and _sample_level(p)) >= 2:
            return name
    return None


def _caption_entries(obj, kind: str) -> TocEntry | None:
    cap = obj.find(_hp("caption"))
    if cap is None:
        return None
    text = " ".join(
        t for p in cap.iter(_hp("p")) if (t := _text(p, autonum=True).strip())
    )
    if not text:
        return None
    target = obj.get("id", "")
    if kind == "figure":
        # Hangul links a 그림 차례 entry to the picture; our figures are 1×2 tables
        pic = next(obj.iter(_hp("pic")), None)
        if pic is not None and pic.get("id"):
            target = pic.get("id")
    return TocEntry(1, re.sub(r"\s+", " ", text), target)


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def _el(xml: str):
    return etree.fromstring(f'<hp:x xmlns:hp="{_HP}">{xml}</hp:x>')[0]


def _hyperlink_begin(field_id: int, target_id: str, kind: str) -> str:
    n, target_type = _TARGETS[kind]
    return (
        f'<hp:ctrl><hp:fieldBegin id="{field_id}" type="HYPERLINK" name="" editable="0" '
        f'dirty="1" zorder="-1" fieldid="{HYPERLINK_FIELD_ID}" metaTag="">'
        f'<hp:parameters cnt="5" name=""><hp:integerParam name="Prop">0</hp:integerParam>'
        f'<hp:stringParam name="Command">?#{target_id};0;{n};0;</hp:stringParam>'
        f'<hp:stringParam name="Category">HWPHYPERLINK_TYPE_HWP</hp:stringParam>'
        f'<hp:stringParam name="TargetType">{target_type}</hp:stringParam>'
        f'<hp:stringParam name="DocOpenType">HWPHYPERLINK_JUMP_CURRENTTAB</hp:stringParam>'
        f"</hp:parameters></hp:fieldBegin></hp:ctrl>"
    )


def _toc_begin(field_id: int) -> str:
    return (
        f'<hp:ctrl><hp:fieldBegin id="{field_id}" type="TABLEOFCONTENTS" name="" '
        f'editable="1" dirty="0" zorder="-1" fieldid="{TOC_FIELD_ID}" metaTag="">'
        f'<hp:parameters cnt="2" name=""><hp:integerParam name="Prop">8</hp:integerParam>'
        f'<hp:stringParam name="Command" xml:space="preserve">{TOC_COMMAND}</hp:stringParam>'
        f"</hp:parameters></hp:fieldBegin></hp:ctrl>"
    )


def _field_end(field_id: int, fieldid: str) -> str:
    return f'<hp:ctrl><hp:fieldEnd beginIDRef="{field_id}" fieldid="{fieldid}"/></hp:ctrl>'


def _strip_layout(p) -> None:
    for lsa in p.findall(_hp("linesegarray")):
        p.remove(lsa)


def _entry_para(template, entry: TocEntry, kind: str):
    """A copy of the template's sample entry *template* showing *entry*, linked to
    its target, with the right tab and an empty page slot."""
    p = copy.deepcopy(template)
    _strip_layout(p)
    runs = p.findall(_hp("run"))
    char = runs[0].get("charPrIDRef", "0") if runs else "0"
    tab = next(template.iter(_hp("tab")))
    for r in runs:
        p.remove(r)
    fid = new_field_id()
    run = _el(f'<hp:run charPrIDRef="{char}"/>')
    run.append(_el(_hyperlink_begin(fid, entry.target_id, kind)))
    t = _el("<hp:t/>")
    t.text = entry.text
    tab = copy.deepcopy(tab)
    tab.tail = None
    t.append(tab)
    run.append(t)
    run.append(_el(_field_end(fid, HYPERLINK_FIELD_ID)))
    run.append(_el("<hp:t/>"))
    p.insert(0, run)
    return p


def _clear_page_number(p) -> None:
    """Drop the sample page number after an entry's right tab (keep ⅰ-style front
    matter numbers, which the template fixes)."""
    seen_tab = False
    for t in list(p.iter(_hp("t"))):
        if seen_tab and _ARABIC_PAGE_RE.match(t.text or ""):
            t.text = ""
        for sub in t:
            if sub.tag == _hp("tab") and sub.get("type") == "2":
                seen_tab = True
                if _ARABIC_PAGE_RE.match(sub.tail or ""):
                    sub.tail = None
            elif seen_tab and _ARABIC_PAGE_RE.match(sub.tail or ""):
                sub.tail = None


def _fill_headings(root, headings: list[TocEntry], result: TocResult, *, toc_field: bool):
    paras = root.findall(_hp("p"))
    sample = [(i, p, lvl) for i, p in enumerate(paras) if _is_entry(p)
              and (lvl := _sample_level(p))]
    if not sample:
        return
    templates: dict[int, etree._Element] = {}
    for _, p, lvl in sample:
        templates.setdefault(lvl, p)
    first_i, last_i = sample[0][0], sample[-1][0]
    # separator between chapters: what sits before the 2nd level-1 sample entry
    gap: list = []
    level1 = [i for i, _, lvl in sample if lvl == 1]
    if len(level1) >= 2:
        prev_entry = max(i for i, _, _ in sample if i < level1[1])
        gap = paras[prev_entry + 1: level1[1]]
    anchor = paras[first_i]
    new: list = []
    max_level = max(templates)
    used = [h for h in headings if h.level <= max_level]
    for h in used:
        tpl = templates.get(h.level)
        if tpl is None:  # template shows fewer levels in between: nearest shallower
            tpl = templates[max(k for k in templates if k <= h.level)]
        if h.level == 1 and new:
            for g in gap:
                gp = copy.deepcopy(g)
                _strip_layout(gp)
                new.append(gp)
        new.append(_entry_para(tpl, h, "outline"))
    if toc_field and new:
        entries = [p for p in new if _is_entry(p)]
        fid = new_field_id()
        entries[0].find(_hp("run")).insert(0, _el(_toc_begin(fid)))
        end = copy.deepcopy(entries[-1])
        for r in end.findall(_hp("run")):
            end.remove(r)
        char = entries[-1].find(_hp("run")).get("charPrIDRef", "0")
        run = _el(f'<hp:run charPrIDRef="{char}"/>')
        run.append(_el(_field_end(fid, TOC_FIELD_ID)))
        run.append(_el("<hp:t/>"))
        end.insert(0, run)
        new.append(end)
    for p in new:
        anchor.addprevious(p)
    for p in paras[first_i: last_i + 1]:
        root.remove(p)
    result.headings = used


def _fill_list(root, kind: str, items: list[TocEntry]) -> bool:
    """Replace the sample entries under the ``표 차례`` / ``그림 차례`` header.

    Returns False if the template has no such list. With no *items*, the header
    goes too (an empty list would only show a dangling title)."""
    word = "표" if kind == "table" else "그림"
    paras = root.findall(_hp("p"))
    head = next((i for i, p in enumerate(paras)
                 if (m := _LIST_HEADER_RE.match(_text(p))) and m.group(1) == word), None)
    if head is None:
        return False
    stop = next((i for i in range(head + 1, len(paras))
                 if _LIST_HEADER_RE.match(_text(paras[i]))), len(paras))
    sample = [p for p in paras[head + 1: stop] if _is_entry(p)]
    if not sample:
        return True
    tpl = sample[0]
    for it in items:
        sample[0].addprevious(_entry_para(tpl, it, kind))
    for p in sample:
        root.remove(p)
    if not items:
        root.remove(paras[head])
    return True


def fill_toc(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    toc_field: bool = True,
) -> TocResult:
    """Rewrite the template's 목차 lists from the document's headings and captions.

    Rewrites *dst* (default: *src* in place) changing only the sections it touches;
    a document without a recognisable 목차 section is copied unchanged and the
    result's :attr:`TocResult.filled` is False. *toc_field* wraps the heading list
    in Hangul's ``TABLEOFCONTENTS`` field (see module docstring).
    """
    from .styles import role_map

    src = Path(src)
    dst = Path(dst) if dst is not None else src
    result = TocResult()
    roles = role_map(src)
    levels = {roles[f"HEADING_{n}"]: n for n in (3, 2, 1) if f"HEADING_{n}" in roles}

    with zipfile.ZipFile(src) as zf:
        parts = _section_parts(zf.namelist())
        raw = {n: zf.read(n) for n in parts}
    sections = {n: etree.fromstring(raw[n]) for n in parts}
    toc_part = find_toc_section(sections)
    overrides: dict[str, bytes] = {}
    if toc_part is not None:
        result.section = toc_part
        after = parts[parts.index(toc_part) + 1:]
        used_ids = {p.get("id") for r in sections.values() for p in r.iter(_hp("p"))}
        headings: list[TocEntry] = []
        for name in after:
            root = sections[name]
            for p in root.findall(_hp("p")):
                lvl = levels.get(p.get("styleIDRef", ""))
                text = re.sub(r"\s+", " ", _text(p)).strip()
                if lvl is None or not text:
                    continue
                if p.get("id", "") in _NO_ID or p.get("id") in {h.target_id for h in headings}:
                    while (nid := str(new_field_id())) in used_ids:
                        pass
                    used_ids.add(nid)
                    p.set("id", nid)
                    overrides[name] = b""
                headings.append(TocEntry(lvl, text, p.get("id")))
            for obj in root.iter(_hp("tbl"), _hp("pic")):
                kind = {"TABLE": "table", "PICTURE": "figure"}.get(obj.get("numberingType"))
                if kind is None or obj.get("id", "") in _NO_ID:
                    continue
                entry = _caption_entries(obj, kind)
                if entry is not None:
                    (result.tables if kind == "table" else result.figures).append(entry)
        toc_root = sections[toc_part]
        _fill_headings(toc_root, headings, result, toc_field=toc_field)
        if not _fill_list(toc_root, "table", result.tables) and result.tables:
            result.warnings.append("목차 섹션에 '표 차례'가 없어 표 차례를 만들지 않았습니다")
        if not _fill_list(toc_root, "figure", result.figures) and result.figures:
            result.warnings.append("목차 섹션에 '그림 차례'가 없어 그림 차례를 만들지 않았습니다")
        for p in toc_root.findall(_hp("p")):
            if _is_entry(p) and not _sample_level(p):
                _clear_page_number(p)
        if not headings:
            result.warnings.append("본문에서 제목(HEADING_1·2) 문단을 찾지 못해 차례가 비었습니다")
        overrides[toc_part] = b""

    for name in overrides:
        overrides[name] = _serialize(raw[name], sections[name])
    if not overrides:
        if src != dst:
            dst.write_bytes(src.read_bytes())
        return result
    tmp = dst.with_suffix(dst.suffix + ".toc.tmp")
    _rewrite_zip_preserving(src, tmp, overrides)
    tmp.replace(dst)
    return result


def _serialize(raw: bytes, root) -> bytes:
    """*root* as bytes, keeping the part's original XML declaration verbatim."""
    head = raw[: raw.index(b"<", raw.index(b"?>"))] if raw.startswith(b"<?xml") else b""
    return head + etree.tostring(root, encoding="UTF-8")
