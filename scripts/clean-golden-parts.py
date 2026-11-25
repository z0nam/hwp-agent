#!/usr/bin/env python3
"""Clear the sample content around the 요약·들어가며·참고문헌 markers (#94).

``examples/정책과제-template.hwpx`` kept the source form's placeholders next to the
``{{summary}}`` / ``{{intro}}`` markers, and they printed in every report:

* section2 (요약) — a three-chapter skeleton after the form table
  (``Ⅰ. 장 제목`` / ``1. 절 제목`` / ``핵심 주제 및 대항목``…) and the sample line
  ``~~~ 참고자료로 활용`` in the 예상 정책 활용 cell
* section3 (들어가며) — empty bullet paragraphs after the marker (bullets print)

It also adds the two paragraph styles ``write`` uses for the 참고문헌 part (#107,
#108), taken from the form's original static list: ``참고문헌 분류`` (the 해외 문헌 /
국내 문헌 header, paraPr 24 · charPr 27) and ``참고문헌`` (an entry, paraPr 29 with
its hanging indent · charPr 5). See :mod:`hwp_agent.ops.report_parts`.

Idempotent. Usage: scripts/clean-golden-parts.py [template.hwpx]
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
SAMPLE_CELL = "~~~ 참고자료로 활용"
STYLES = (  # name, paraPr, charPr
    ("참고문헌 분류", "24", "27"),
    ("참고문헌", "29", "5"),
)


def hp(tag: str) -> str:
    return f"{{{HP}}}{tag}"


def text(p) -> str:
    return "".join(t.text or "" for t in p.iter(hp("t"))).strip()


def has_ctrl(p) -> bool:
    return any(p.find(".//" + hp(t)) is not None for t in ("secPr", "tbl", "ctrl", "pic"))


def clean(src: Path, dst: Path) -> list[str]:
    done: list[str] = []
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(src) as z:
        def load(name):
            return z.read(name), etree.fromstring(z.read(name))

        def dump(orig, root):
            decl = orig[: orig.index(b"?>") + 2]
            return decl + etree.tostring(root, encoding="UTF-8", xml_declaration=False)

        # 요약: the skeleton between the form table and {{summary}}, and the sample cell
        orig, s2 = load("Contents/section2.xml")
        paras = s2.findall(hp("p"))
        table_i = next(i for i, p in enumerate(paras) if p.find(".//" + hp("tbl")) is not None)
        marker_i = next(i for i, p in enumerate(paras) if "{{summary}}" in text(p))
        between = paras[table_i + 1 : marker_i]
        if any(text(p) for p in between):
            for p in between[:-1]:  # keep one blank line before the summary text
                if not has_ctrl(p):
                    s2.remove(p)
            done.append("summary skeleton")
        for t in s2.iter(hp("t")):
            if t.text and SAMPLE_CELL in t.text:
                t.text = t.text.replace(SAMPLE_CELL, "")
                done.append("summary sample cell")
        if done:
            out["Contents/section2.xml"] = dump(orig, s2)

        # 들어가며: empty (bulleted) paragraphs after {{intro}}
        orig, s3 = load("Contents/section3.xml")
        paras = s3.findall(hp("p"))
        marker_i = next(i for i, p in enumerate(paras) if "{{intro}}" in text(p))
        tail = [p for p in paras[marker_i + 1 :] if not text(p) and not has_ctrl(p)]
        if tail:
            for p in tail:
                s3.remove(p)
            out["Contents/section3.xml"] = dump(orig, s3)
            done.append(f"intro blank bullets ×{len(tail)}")

        # 참고문헌 styles
        raw = z.read("Contents/header.xml")
        head = raw.decode("utf-8")
        missing = [s for s in STYLES if f'name="{s[0]}"' not in head]
        if missing:
            root = etree.fromstring(raw)
            styles = root.find(f".//{{{HH}}}styles")
            next_id = 1 + max(int(s.get("id")) for s in styles)
            for name, para, char in missing:
                etree.SubElement(styles, f"{{{HH}}}style", {
                    "id": str(next_id), "type": "PARA", "name": name, "engName": "",
                    "paraPrIDRef": para, "charPrIDRef": char,
                    "nextStyleIDRef": str(next_id), "langID": "1042", "lockForm": "0",
                })
                next_id += 1
            styles.set("itemCnt", str(len(styles)))
            out["Contents/header.xml"] = dump(raw, root)
            done.append("reference styles")

        with zipfile.ZipFile(dst, "w") as zout:
            for info in z.infolist():
                zout.writestr(info, out.get(info.filename, z.read(info.filename)))
    return done


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/정책과제-template.hwpx")
    tmp = path.with_suffix(".tmp.hwpx")
    changed = clean(path, tmp)
    tmp.replace(path)
    print("cleaned:", ", ".join(changed) or "nothing (already clean)")
