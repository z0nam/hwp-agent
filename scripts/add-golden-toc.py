#!/usr/bin/env python3
"""Give the golden template a fillable 목차 section and Hangul's 차례 styles (#33).

``build-golden-template.py`` emptied section1 (the 목차) down to an instruction to
make the 차례 in Hangul, so ``build`` had nothing to fill there.  This rebuilds it
from the house form's own 목차 section (``examples/house-forms/정책과제_서식.normalized.hwpx``,
the same form the golden template is made from — same header, same section setup),
cut down to a neutral sample that :func:`hwp_agent.ops.toc.fill_toc` recognises:

* 차례 — ``요약 ⇥ ⅰ``, ``Ⅰ. 장 제목`` / ``1. 절 제목`` / ``1) 항 제목``, the chapter
  separator, ``Ⅱ. 장 제목`` / ``1. 절 제목``, then ``참고문헌`` · ``부록``;
* 표 차례 / 그림 차례 — the form's header pages with two neutral entries each.

No text of the form's sample report is kept: every sample entry is relabelled and its
page number dropped.  The header gets Hangul's 차례 styles (``차례 제목`` · ``차례 1/2/3``)
with the sample entries' look, appended at the end
of the style list, so a 차례 새로 고침 in Hangul keeps the form's look.  Decorations
stay out of Hangul's 표·그림 차례 (numberingType NONE, #109).

Idempotent.  Usage: scripts/add-golden-toc.py [template.hwpx] [house-form.hwpx]
"""

from __future__ import annotations

import copy
import sys
import zipfile
from pathlib import Path

from lxml import etree

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from hwp_agent.ops.container import _rewrite_zip_preserving  # noqa: E402
from hwp_agent.ops.numbering import neutralize_uncaptioned_numbering  # noqa: E402
from hwp_agent.ops.toc import (  # noqa: E402
    _LIST_HEADER_RE,
    _clear_page_number,
    _ensure_toc_styles,
    _hp,
    _is_entry,
    _sample_level,
    _serialize,
    _strip_layout,
    _text,
)

TEMPLATE = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "examples" / "정책과제-template.hwpx"
HOUSE = (Path(sys.argv[2]) if len(sys.argv) > 2
         else REPO / "examples" / "house-forms" / "정책과제_서식.normalized.hwpx")
SECTION = "Contents/section1.xml"
HEADER = "Contents/header.xml"


def relabel(p, label: str) -> None:
    """Make sample entry *p* read ``label ⇥`` (no page number), in its first run's look."""
    tab = copy.deepcopy(next(t for t in p.iter(_hp("tab")) if t.get("type") == "2"))
    tab.tail = None
    runs = p.findall(_hp("run"))
    keep = next(r for r in runs if (r.findtext(_hp("t")) or "").strip())
    for r in runs:
        if r is not keep:
            p.remove(r)
    for child in list(keep):
        keep.remove(child)
    t = etree.SubElement(keep, _hp("t"))
    t.text = label
    t.append(tab)
    _strip_layout(p)


def skeleton(house_root) -> list:
    paras = house_root.findall(_hp("p"))
    entries = [(i, lvl) for i, p in enumerate(paras) if _is_entry(p)
               and (lvl := _sample_level(p))]
    level1 = [i for i, lvl in entries if lvl == 1]
    a, b = level1[0], level1[1]
    pick = lambda lo, hi, lv: next(i for i, lvl in entries if lo < i < hi and lvl == lv)  # noqa: E731
    l2a, l3a, l2b = pick(a, b, 2), pick(a, b, 3), pick(b, len(paras), 2)
    gap_from = max(i for i, _ in entries if i < b) + 1
    last_num = entries[-1][0]
    heads = [i for i, p in enumerate(paras) if _LIST_HEADER_RE.match(_text(p))]
    t_head, f_head = heads
    t_entries = [i for i in range(t_head, f_head) if _is_entry(paras[i])]
    f_entries = [i for i in range(f_head, len(paras)) if _is_entry(paras[i])]
    t_end = max(i for i in range(t_entries[-1], f_head) if paras[i].get("pageBreak") == "1")

    labels = {a: "Ⅰ. 장 제목", l2a: "1. 절 제목", l3a: "1) 항 제목",
              b: "Ⅱ. 장 제목", l2b: "1. 절 제목",
              t_entries[0]: "<표 Ⅰ-1> 표 제목", t_entries[1]: "<표 Ⅰ-2> 표 제목",
              f_entries[0]: "[그림 Ⅰ-1] 그림 제목", f_entries[1]: "[그림 Ⅰ-2] 그림 제목"}
    order = (
        list(range(a)) + [a, l2a, l3a] + list(range(gap_from, b)) + [b, l2b]
        + list(range(last_num + 1, t_entries[0])) + t_entries[:2]
        + list(range(t_end, f_entries[0])) + f_entries[:2]
        + list(range(f_entries[-1] + 1, len(paras)))
    )
    out = []
    for i in order:
        p = copy.deepcopy(paras[i])
        if i in labels:
            relabel(p, labels[i])
        elif _is_entry(p):
            _clear_page_number(p)  # 참고문헌·부록 (요약 ⅰ is kept)
            _strip_layout(p)
        out.append(p)
    return out


def main() -> int:
    with zipfile.ZipFile(HOUSE) as zh:
        house = etree.fromstring(zh.read(SECTION))
    with zipfile.ZipFile(TEMPLATE) as zt:
        sraw, hraw = zt.read(SECTION), zt.read(HEADER)
    sec = etree.fromstring(sraw)
    for p in sec.findall(_hp("p")):
        sec.remove(p)
    new = skeleton(house)
    for p in new:
        sec.append(p)

    looks = {}
    for p in new:
        if _is_entry(p) and (lvl := _sample_level(p)) and lvl not in looks:
            looks[lvl] = (p.get("paraPrIDRef"), p.find(_hp("run")).get("charPrIDRef"))
    head = etree.fromstring(hraw)
    ids = _ensure_toc_styles(head, looks)

    tmp = TEMPLATE.with_suffix(".toc.tmp")
    _rewrite_zip_preserving(TEMPLATE, tmp, {SECTION: _serialize(sraw, sec),
                                            HEADER: _serialize(hraw, head)})
    neutralize_uncaptioned_numbering(tmp)
    tmp.replace(TEMPLATE)
    print(f"{TEMPLATE}: 목차 skeleton {len(new)} paragraphs, styles {ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
