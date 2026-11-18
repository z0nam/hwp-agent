#!/usr/bin/env python3
"""Add the ``{{summary}}`` / ``{{intro}}`` / ``{{references}}`` markers (#94).

``examples/정책과제-template.hwpx`` only carried ``{{body}}``, so ``hwp-agent build``
filled just the body section and left 요약·들어가며·참고문헌 empty (a references list
in the Markdown landed at the end of the body).  This edits the golden template in
place and is idempotent:

* section2 (요약 양식)  — ``{{summary}}`` paragraph appended after the form
* section3 (들어가며)   — ``{{intro}}`` paragraph after the AI instruction
* section5 (참고문헌)   — the static 해외/국내/기타/통계 header + blank item paragraphs
  are removed (a single marker can't sit under four headers); ``{{references}}``
  follows the instruction.  Write the groups in the Markdown (``**해외 문헌**`` lines).

Usage: scripts/add-golden-markers.py [template.hwpx]
"""
from __future__ import annotations

import copy
import sys
import zipfile
from pathlib import Path

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def hp(tag: str) -> str:
    return f"{{{HP}}}{tag}"


def text(p) -> str:
    return "".join(t.text or "" for t in p.iter(hp("t"))).strip()


def has_ctrl(p) -> bool:
    return p.find(".//" + hp("secPr")) is not None or p.find(".//" + hp("tbl")) is not None


def marker_para(src, value: str):
    p = copy.deepcopy(src)
    p.set("styleIDRef", "0")
    p.set("paraPrIDRef", "24")
    for r in p.findall(hp("run"))[1:]:
        p.remove(r)
    for extra in p.findall(hp("linesegarray")):
        p.remove(extra)
    run = p.find(hp("run"))
    for c in list(run):
        run.remove(c)
    etree.SubElement(run, hp("t")).text = value
    return p


def plain_source(root):
    for p in root.findall(hp("p")):
        if not has_ctrl(p) and p.find(hp("run")) is not None:
            return p
    raise RuntimeError("no clone source paragraph")


def add_markers(src: Path, dst: Path) -> list[str]:
    done: list[str] = []
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(src) as z:
        def load(name):
            return z.read(name), etree.fromstring(z.read(name))

        def dump(name, orig, root):
            decl = orig[: orig.index(b"?>") + 2]
            return decl + etree.tostring(root, encoding="UTF-8", xml_declaration=False)

        # 요약
        orig, s2 = load("Contents/section2.xml")
        if "{{summary}}" not in orig.decode("utf-8"):
            s2.append(marker_para(plain_source(s2), "{{summary}}"))
            out["Contents/section2.xml"] = dump("Contents/section2.xml", orig, s2)
            done.append("summary")
        # 들어가며
        orig, s3 = load("Contents/section3.xml")
        if "{{intro}}" not in orig.decode("utf-8"):
            instr = next((p for p in s3.findall(hp("p")) if "들어가며(서문)" in text(p)), None)
            m = marker_para(plain_source(s3), "{{intro}}")
            if instr is not None:
                instr.addnext(m)
            else:
                s3.append(m)
            out["Contents/section3.xml"] = dump("Contents/section3.xml", orig, s3)
            done.append("intro")
        # 참고문헌
        orig, s5 = load("Contents/section5.xml")
        if "{{references}}" not in orig.decode("utf-8"):
            paras = s5.findall(hp("p"))
            instr = next((p for p in paras if "참고문헌:" in text(p)), None)
            keep_first = paras[0]  # carries the section's secPr + the title
            for p in paras[1:]:
                if p is instr or has_ctrl(p):
                    continue
                s5.remove(p)
            m = marker_para(plain_source(s5), "{{references}}")
            (instr if instr is not None else keep_first).addnext(m)
            out["Contents/section5.xml"] = dump("Contents/section5.xml", orig, s5)
            done.append("references")
        with zipfile.ZipFile(dst, "w") as zout:
            for info in z.infolist():
                zout.writestr(info, out.get(info.filename, z.read(info.filename)))
    return done


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/정책과제-template.hwpx")
    tmp = path.with_suffix(".tmp.hwpx")
    added = add_markers(path, tmp)
    tmp.replace(path)
    print("added markers:", added or "none (already present)")
