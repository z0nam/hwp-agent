"""요약·들어가며·참고문헌 parts of a report written into their own template sections (#94).

* :func:`split_report_parts` — a single ``write`` manuscript may carry the parts
  under the bare H1s ``# 요약`` · ``# 들어가며`` · ``# 참고문헌``; they are routed to the
  ``{{summary}}`` · ``{{intro}}`` · ``{{references}}`` markers like ``build``'s parts.
* :func:`pop_summary_table` / :func:`fill_summary_form` (#106) — a ``| 구분 | 주요내용 |``
  Markdown table fills the 요약 form's own cells, row by row by the 구분 label,
  instead of landing as a new table after it.
* :func:`reference_headers` / :func:`format_references` (#107, #108) — in the
  참고문헌 part a line that is bold on its own (``**해외 문헌**``) or a heading is a
  group header and takes the template's ``참고문헌 분류`` style; every other entry
  takes ``참고문헌`` (hanging indent). A template without those styles is left as
  written.
"""

from __future__ import annotations

import copy
import re
import zipfile
from pathlib import Path

from lxml import etree

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"

#: bare H1 → part name (exact titles only; anything else stays in the body)
PART_TITLES = {"요약": "summary", "들어가며": "intro", "참고문헌": "references"}
PART_ORDER = ("summary", "intro", "body", "references")
REF_HEADER_STYLE = "참고문헌 분류"
REF_ENTRY_STYLE = "참고문헌"

_H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:")


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


def split_report_parts(markdown: str) -> list[tuple[str, str]] | None:
    """``[(marker, markdown), …]`` when *markdown* has a bare ``# 요약`` /
    ``# 들어가며`` / ``# 참고문헌``, else None.

    Each such H1 opens its part, which runs to the next H1 of any kind; the H1
    itself is dropped (the template section has its own title). Everything else is
    the body. Fenced code is not scanned.
    """
    markdown, notes = _pull_footnote_defs(markdown)
    buckets: dict[str, list[str]] = {}
    current = "body"
    found = False
    fenced = False
    for line in markdown.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced and (m := _H1_RE.match(line)):
            name = PART_TITLES.get(m.group(1).strip())
            if name:
                current, found = name, True
                buckets.setdefault(name, [])
                continue
            current = "body"
        buckets.setdefault(current, []).append(line)
    if not found:
        return None
    parts = []
    for name in PART_ORDER:
        text = "\n".join(buckets.get(name, [])).strip()
        if not text:
            continue
        # each part is filled on its own: give it the definitions it refers to
        used = [d for fid, d in notes if f"[^{fid}]" in text]
        if name == "body":  # one nothing refers to keeps write's unused-note warning
            used += [d for fid, d in notes if not any(
                f"[^{fid}]" in "\n".join(v) for v in buckets.values()
            )]
        parts.append(("{{" + name + "}}", "\n\n".join([text, *used]) + "\n"))
    return parts


def _pull_footnote_defs(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """Take ``[^id]: …`` definitions (with indented continuation lines) out of
    *markdown*: ``(rest, [(id, definition_text), …])``. Fenced code is left alone."""
    out: list[str] = []
    notes: list[tuple[str, str]] = []
    lines = markdown.splitlines()
    fenced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            fenced = not fenced
        m = None if fenced else _FOOTNOTE_DEF_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        block = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and lines[i][:1] in (" ", "\t"):
            block.append(lines[i])
            i += 1
        notes.append((m.group(1), "\n".join(block)))
    return "\n".join(out), notes


# --- 요약 form (#106) ---------------------------------------------------------


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _key(label: str) -> str:
    """구분 label as compared: no spaces, no parenthesised gloss."""
    return re.sub(r"\(.*?\)|\s", "", label)


def pop_summary_table(markdown: str) -> tuple[list[tuple[str, str]], str]:
    """Take the ``| 구분 | 주요내용 |`` table out of *markdown*: ``(rows, rest)``."""
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|") or i + 1 >= len(lines):
            continue
        head = _cells(line)
        if len(head) < 2 or _key(head[0]) != "구분" or not _key(head[1]).startswith("주요내용"):
            continue
        if not re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            continue
        j = i + 2
        rows = []
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            c = _cells(lines[j])
            rows.append((c[0], c[1] if len(c) > 1 else ""))
            j += 1
        return rows, "\n".join(lines[:i] + lines[j:])
    return [], markdown


def _cell_lines(cell: str) -> list[tuple[int, str]]:
    """``(level, text)`` per ``<br>``-separated line; ``  - `` (indented) is level 2."""
    out = []
    for raw in re.split(r"<br\s*/?>", cell, flags=re.I):
        if not raw.strip():
            continue
        level = 2 if re.match(r"^(\s{2,}|&nbsp;)+[-*]\s", raw) else 1
        txt = re.sub(r"^(\s|&nbsp;)*([-*○◦·]\s+)?", "", raw)
        txt = re.sub(r"\*\*|__", "", txt).strip()
        out.append((level, txt))
    return out


def _para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(_hp("t")))


def _set_text(p, value: str) -> None:
    """Make *p* one run of *value* (its first run's char shape); drop layout caches."""
    runs = p.findall(_hp("run"))
    first = runs[0]
    for r in runs[1:]:
        p.remove(r)
    for c in list(first):
        first.remove(c)
    etree.SubElement(first, _hp("t")).text = value
    for seg in p.findall(_hp("linesegarray")):
        p.remove(seg)


def _fill_choice(paras, items: list[str]) -> list[str]:
    """Tick (□→■) the 정책 활용 분야 boxes named in *items*; returns unmatched items."""
    left = list(items)
    for p in paras:
        label = _para_text(p).strip()
        if not label.startswith("□"):
            continue
        name = label[1:].strip()
        for item in list(left):
            k = _key(item.split(":")[0])
            if k.startswith("기타") if name.startswith("기타") else k == _key(name):
                if name.startswith("기타"):
                    detail = re.sub(r"^기타\s*[:(]?\s*|\)\s*$", "", item).strip()
                    _set_text(p, f"■ 기타({detail})" if detail else "■ 기타")
                else:
                    _set_text(p, "■" + label[1:])
                left.remove(item)
                break
    return left


def find_summary_form(section_root):
    """The 요약 form table (first row 구분 | 주요내용…) in a section element, or None."""
    for tbl in section_root.iter(_hp("tbl")):
        tr = tbl.find(_hp("tr"))
        cells = tr.findall(_hp("tc")) if tr is not None else []
        if len(cells) >= 2 and _key(_para_text(cells[0])) == "구분":
            return tbl
    return None


def fill_summary_form(path: str | Path, part: str, rows: list[tuple[str, str]]) -> list[str]:
    """Write *rows* (구분, 주요내용) into the 요약 form table of section *part*
    (e.g. ``Contents/section2.xml``); returns warnings."""
    path = Path(path)
    name = part
    with zipfile.ZipFile(path) as z:
        raw = z.read(name)
    root = etree.fromstring(raw)
    form = find_summary_form(root)
    if form is None:
        return ["요약 양식 표(구분/주요내용)를 찾지 못해 요약 표를 채우지 못했습니다"]
    # 구분 label → its content cells; the 정책 활용 분야 boxes run on over a second
    # row that has no label of its own (every cell a □ choice)
    by_key: dict[str, list] = {}
    last = None
    for tr in form.findall(_hp("tr"))[1:]:
        tcs = tr.findall(_hp("tc"))
        first = _para_text(tcs[0]).strip() if tcs else ""
        if first.startswith("□") and last is not None:
            by_key[last] += tcs
        elif len(tcs) >= 2:
            last = _key(" ".join(_para_text(p) for p in tcs[0].iter(_hp("p"))))
            by_key[last] = tcs[1:]
    warnings = []
    for label, content in rows:
        tcs = by_key.get(_key(label))
        if not tcs:
            warnings.append(f"요약 표에 '{label}' 행이 없습니다 — 건너뜀")
            continue
        boxes = [p for tc in tcs for p in tc.iter(_hp("p"))]
        if any(_para_text(p).strip().startswith("□") for p in boxes):
            items = [t for _, t in _cell_lines(content)]
            items = [i.strip() for t in items for i in re.split(r"[,，]", t) if i.strip()]
            for miss in _fill_choice(boxes, items):
                warnings.append(f"정책 활용 분야에 '{miss}' 항목이 없습니다 — 건너뜀")
            continue
        sub = tcs[0].find(_hp("subList"))
        paras = sub.findall(_hp("p"))
        protos = {1: paras[0], 2: paras[1] if len(paras) > 1 else paras[0]}
        lines = _cell_lines(content)
        if not lines:
            continue
        for p in paras:
            sub.remove(p)
        for level, txt in lines:
            p = copy.deepcopy(protos[level])
            _set_text(p, txt)
            sub.append(p)
    _write_part(path, name, raw, root)
    return warnings


# --- 참고문헌 (#107, #108) ------------------------------------------------------


def reference_headers(markdown: str) -> list[str]:
    """Group header texts in a 참고문헌 part: lines bold on their own, or headings."""
    out = []
    for line in markdown.splitlines():
        s = line.strip()
        m = re.fullmatch(r"\*\*(.+?)\*\*|__(.+?)__|#{1,6}\s+(.+?)\s*#*", s)
        if m:
            out.append(next(g for g in m.groups() if g).strip())
    return out


def _style_ids(header_xml: bytes) -> dict[str, tuple[str, str, str]]:
    root = etree.fromstring(header_xml)
    return {
        s.get("name"): (s.get("id"), s.get("paraPrIDRef"), s.get("charPrIDRef"))
        for s in root.iter(f"{{{_HH}}}style")
    }


def format_references(path: str | Path, part: str, headers: list[str]) -> int:
    """Give the 참고문헌 paragraphs of section *part* the template's reference styles.

    Paragraphs after the section title (the first paragraph reading 참고문헌) whose
    text is one of *headers* become ``참고문헌 분류`` headers (one blank line before,
    except the first, and after); other body paragraphs become ``참고문헌`` entries.
    Returns the paragraphs restyled; 0 when the template lacks the styles.
    """
    path = Path(path)
    name = part
    with zipfile.ZipFile(path) as z:
        styles = _style_ids(z.read("Contents/header.xml"))
        raw = z.read(name)
    if REF_HEADER_STYLE not in styles or REF_ENTRY_STYLE not in styles:
        return 0
    head_id, head_para, head_char = styles[REF_HEADER_STYLE]
    entry_id, entry_para, entry_char = styles[REF_ENTRY_STYLE]
    root = etree.fromstring(raw)
    paras = root.findall(_hp("p"))
    start = next(
        (i + 1 for i, p in enumerate(paras) if _para_text(p).strip() == "참고문헌"), 0
    )
    wanted = {h.strip() for h in headers}
    changed = 0
    first = True
    for p in paras[start:]:
        if p.find(f".//{_hp('tbl')}") is not None or p.find(f".//{_hp('secPr')}") is not None:
            continue
        txt = _para_text(p).strip()
        if not txt:
            continue
        if txt in wanted:
            p.set("styleIDRef", head_id)
            p.set("paraPrIDRef", head_para)
            _set_text(p, txt)
            p.find(_hp("run")).set("charPrIDRef", head_char)
            blank = copy.deepcopy(p)
            _set_text(blank, "")
            blank.find(_hp("run")).set("charPrIDRef", entry_char)
            if not first:
                p.addprevious(copy.deepcopy(blank))
            p.addnext(blank)
        else:
            p.set("styleIDRef", entry_id)
            p.set("paraPrIDRef", entry_para)
            for seg in p.findall(_hp("linesegarray")):
                p.remove(seg)
        first = False
        changed += 1
    if changed:
        _write_part(path, name, raw, root)
    return changed


def _write_part(path: Path, name: str, raw: bytes, root) -> None:
    from .container import _rewrite_zip_preserving

    decl = raw[: raw.index(b"?>") + 2] if raw.startswith(b"<?xml") else b""
    body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    tmp = path.with_name(path.name + ".part.tmp")
    _rewrite_zip_preserving(path, tmp, {name: decl + body})
    tmp.replace(path)
