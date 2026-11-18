"""정책이슈브리프: split the body into chapters and derive the chapter slots (#91 2단계).

The brief template (``examples/정책이슈브리프-template.hwpx``, built from a published
brief by ``scripts/build-brief-template.py``) has one section per chapter group, like
the published brief: chapter 1 in section0, chapter 2 in section1, and **every later
chapter in section2**. Its markers are ``{{ch1}}`` ``{{ch2}}`` ``{{ch3}}`` (the body of
each section) and the title slots ``{{ch1_title}}`` ``{{ch2_title}}`` ``{{ch3_title}}``
(the section's opening chapter heading). The chapter wings (masterpages) read
``{{wing_1}}``…``{{wing_3}}``; the Contents page ``{{toc_1}}``…``{{toc_4}}``.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_H1_RE = re.compile(r"^#\s+(.*\S)\s*$")
_APPENDIX_RE = re.compile(r"^\s*부록")
_ROMAN_RE = re.compile(r"^\s*(?:[IVXⅠ-Ⅻ]+|\d+)\s*[.)]\s*")
SECTIONS = 3  # chapter sections in the template


@dataclass
class Chapter:
    title: str  # the H1 text as written ("Ⅰ. 들어가며")
    body: str  # everything up to the next H1


@dataclass
class BriefParts:
    parts: dict[str, str] = field(default_factory=dict)  # "ch1" → markdown
    slots: dict[str, str] = field(default_factory=dict)  # title / wing / toc slots
    warnings: list[str] = field(default_factory=list)


def is_brief_template(template: Path | str) -> bool:
    """True when *template* carries the ``{{ch1}}`` chapter marker."""
    try:
        with zipfile.ZipFile(template) as z:
            return any(
                b"{{ch1}}" in z.read(n)
                for n in z.namelist()
                if n.startswith("Contents/section") and n.endswith(".xml")
            )
    except (OSError, zipfile.BadZipFile):
        return False


def split_chapters(markdown: str) -> list[Chapter]:
    """Cut *markdown* at its ``# `` headings. Text before the first H1 belongs to the
    first chapter (it is kept in front of that chapter's body)."""
    chapters: list[Chapter] = []
    cur: Chapter | None = None
    pre: list[str] = []
    fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else _H1_RE.match(line)
        if m:
            cur = Chapter(m.group(1).strip(), "")
            chapters.append(cur)
            continue
        if cur is None:
            pre.append(line)
        else:
            cur.body += line + "\n"
    if chapters and any(s.strip() for s in pre):
        chapters[0].body = "\n".join(pre).strip("\n") + "\n\n" + chapters[0].body
    return chapters


def _plain_title(title: str) -> str:
    return _ROMAN_RE.sub("", title, count=1).strip()


def brief_parts(markdown: str) -> BriefParts:
    """Chapter markdown per section marker and the slot values derived from the H1s."""
    out = BriefParts()
    chapters = split_chapters(markdown)
    if not chapters:
        out.warnings.append("정책이슈브리프: 본문에 '# ' 장 제목이 없어 장 구역을 채우지 못했습니다")
        return out
    # chapter k (k = 1, 2) → its own section; chapters 3… share the last one
    for k in range(SECTIONS):
        if k < SECTIONS - 1:
            group = chapters[k : k + 1]
        else:
            group = chapters[k:]
        if not group:
            out.parts[f"ch{k + 1}"] = ""
            out.slots[f"ch{k + 1}_title"] = ""
            out.slots[f"wing_{k + 1}"] = ""
            out.warnings.append(
                f"정책이슈브리프: 장이 {len(chapters)}개뿐이라 {k + 1}번째 장 구역은 비워 둠"
            )
            continue
        first, rest = group[0], group[1:]
        text = first.body
        for ch in rest:  # later chapters keep their own heading inside the section
            text += f"\n# {ch.title}\n{ch.body}"
        out.parts[f"ch{k + 1}"] = text
        out.slots[f"ch{k + 1}_title"] = first.title
        wing = [c.title for c in group if not _APPENDIX_RE.match(c.title)]
        out.slots[f"wing_{k + 1}"] = " · ".join(wing) if wing else first.title
    for k, ch in enumerate(chapters[:4], start=1):
        if not _APPENDIX_RE.match(ch.title):
            out.slots[f"toc_{k}"] = _plain_title(ch.title)
    return out
