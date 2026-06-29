"""Build a skeleton "golden template" from a JRI report 서식 (one-off, provenance).

Produces ``examples/정책과제-template.hwpx`` from the institute's 정책과제 report form
(source: NAVERWORKS 공용드라이브 `0.서식(과제 관련)/보고서 서식(유형별)/정책과제_서식.hwpx`,
which is actually a full worked example, "제주 경제전망 2026"). The example body is
stripped to a reusable skeleton:

  - cover (section0) / colophon (section6) → {{slots}} (report_number, title, …)
  - TOC (section1) / 들어가며 (section3) / 참고문헌 (section5) → cleared + AI:INSTRUCTION
  - 요약 양식 (section2) → kept as-is (already a blank form)
  - body (section4) → AI:INSTRUCTION + {{body}} marker + a neutralized
    {{table_template}} reference table (trimmed to 3 rows)
  - a new AI:INSTRUCTION style (id 19) is declared in header.xml
  - orphaned BinData images (example charts no longer referenced) are dropped

Input is the *normalized* form (run `hwp-agent normalize` first so AI:HEADING_n /
AI:BULLET_n are declared). Container-preserving (Hangul 보안경고 회피): only
header.xml + section0..6 + content.hpf are rewritten; every other part is re-emitted
byte-for-byte.

NOTE: the cover/colophon text replacements match this example's exact strings
(e.g. "제주 경제전망 2026"). The other three forms (기반·센터·전략과제) share the
style system but carry *different* example content, so those strings need adapting
— or, better, generalize this into a `hwp-agent`-native "make machine-friendly"
command (see docs/author-backlog.md / next steps).

    usage: python scripts/build-golden-template.py <normalized.hwpx> <out.hwpx>
"""
from __future__ import annotations

import copy
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree

from hwp_agent.ops.form import _set_cell_text_overwrite

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
def hp(t): return f"{{{HP}}}{t}"

SRC = Path(sys.argv[1])   # 정책_norm.hwpx
DST = Path(sys.argv[2])   # 정책과제_template.hwpx

INSTR_STYLE = "19"        # new AI:INSTRUCTION style

# ---------- generic helpers ----------
def para_text(p):
    return "".join(t.text or "" for t in p.iter(hp("t")))

def set_para_text(p, value):
    """Collapse a paragraph's visible text to *value* in its first <hp:t>;
    leaves <hp:ctrl>/<hp:secPr> runs intact."""
    ts = [t for r in p.findall(hp("run")) for t in r.findall(hp("t"))]
    if ts:
        for c in list(ts[0]):
            ts[0].remove(c)
        ts[0].text = value
        for t in ts[1:]:
            for c in list(t):
                t.remove(c)
            t.text = ""
    else:
        runs = p.findall(hp("run"))
        r = runs[0] if runs else etree.SubElement(p, hp("run"))
        etree.SubElement(r, hp("t")).text = value

def has_secpr(p):
    return p.find(".//" + hp("secPr")) is not None

def has_tbl(p):
    return p.find(".//" + hp("tbl")) is not None

def clean_clone_source(root):
    """A simple top-level <hp:p> (run+t, no tbl/secPr) to clone instruction/marker paras."""
    for p in root.findall(hp("p")):
        if has_tbl(p) or has_secpr(p):
            continue
        if [t for r in p.findall(hp("run")) for t in r.findall(hp("t"))]:
            return p
    raise RuntimeError("no clean clone source")

def make_para(src, value, style_id, para_pr="24"):
    clone = copy.deepcopy(src)
    clone.set("styleIDRef", str(style_id))
    clone.set("paraPrIDRef", para_pr)
    # strip extra runs, keep one
    runs = clone.findall(hp("run"))
    for r in runs[1:]:
        clone.remove(r)
    set_para_text(clone, value)
    return clone

# ---------- caption (from the verified bundled-template caption) ----------
def build_caption(width):
    xml = (
        '<hp:caption xmlns:hp="__NS__" side="TOP" fullSz="0"'
        ' width="__W__" gap="850" lastWidth="__W__">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK"'
        ' vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0"'
        ' textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        '<hp:p id="2147483648" paraPrIDRef="61" styleIDRef="14"'
        ' pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="49">'
        '<hp:t>&lt;표 Ⅰ-</hp:t>'
        '<hp:ctrl><hp:autoNum num="1" numType="TABLE">'
        '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar=""'
        ' suffixChar="" supscript="0"/>'
        '</hp:autoNum></hp:ctrl>'
        '<hp:t>&gt; {{table_template}}</hp:t>'
        '</hp:run></hp:p></hp:subList></hp:caption>'
    ).replace("__NS__", HP).replace("__W__", str(width))
    return etree.fromstring(xml.encode("utf-8"))

# ---------- load sections ----------
zin = zipfile.ZipFile(SRC)
def load(name):
    return etree.fromstring(zin.read(name))
def dump(name, root):
    orig = zin.read(name)
    decl = orig[:orig.index(b"?>") + 2]
    body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    return decl + body

overrides = {}

# ===== header.xml : add AI:INSTRUCTION style =====
h = zin.read("Contents/header.xml").decode("utf-8")
assert 'engName="AI:INSTRUCTION"' not in h
style = ('<hh:style id="19" type="PARA" name="AI지침" engName="AI:INSTRUCTION" '
         'paraPrIDRef="24" charPrIDRef="5" nextStyleIDRef="19" langID="1042" lockForm="0"/>')
h = h.replace("</hh:styles>", style + "</hh:styles>", 1)
h = h.replace('<hh:styles itemCnt="19">', '<hh:styles itemCnt="20">', 1)
assert 'itemCnt="20"' in h and 'engName="AI:INSTRUCTION"' in h
overrides["Contents/header.xml"] = h.encode("utf-8")

# ===== section0 : cover -> slots =====
s0 = load("Contents/section0.xml")
cover = {
    "정책연구 2026-01": "정책연구 {{report_number}}",
    "제주 경제전망 2026": "{{title}}",
    ": 선순환과 악순환의 전환점": "{{subtitle}}",
    "○○○·△△△": "{{authors}}",
}
for p in s0.iter(hp("p")):
    txt = para_text(p).strip()
    if txt in cover:
        set_para_text(p, cover[txt])
overrides["Contents/section0.xml"] = dump("Contents/section0.xml", s0)

# ===== section1 : TOC (literal example) -> cleared + instruction =====
s1 = load("Contents/section1.xml")
ps1 = s1.findall(hp("p"))
for p in ps1:
    set_para_text(p, "")
# first non-secPr para -> instruction
for p in ps1:
    if not has_secpr(p):
        p.set("styleIDRef", INSTR_STYLE)
        set_para_text(p, "목차 — 한글에서 [도구 > 차례/색인 > 차례 만들기]로 "
                          "자동 생성하세요. (write 시 제거됨)")
        break
overrides["Contents/section1.xml"] = dump("Contents/section1.xml", s1)

# ===== section2 : 요약 양식 -> keep as-is =====

# ===== section3 : 들어가며 -> keep heading, blank prose, add instruction =====
s3 = load("Contents/section3.xml")
done_instr = False
for p in s3.findall(hp("p")):
    txt = para_text(p).strip()
    if txt == "들어가며":
        continue
    if txt:
        if not done_instr:
            p.set("styleIDRef", INSTR_STYLE)
            set_para_text(p, "들어가며(서문): 연구 배경·목적과 보고서 구성을 "
                             "2~4문단으로 작성하세요. (write 시 제거됨)")
            done_instr = True
        else:
            set_para_text(p, "")
overrides["Contents/section3.xml"] = dump("Contents/section3.xml", s3)

# ===== section4 : body -> instruction + {{body}} + table_template =====
s4 = load("Contents/section4.xml")
src = clean_clone_source(s4)
children = list(s4)
sec_p = next(p for p in s4.findall(hp("p")) if has_secpr(p))
table_p = next(p for p in s4.findall(hp("p")) if has_tbl(p))

# neutralize the reference table
tbl = table_p.find(".//" + hp("tbl"))
rows = tbl.findall(hp("tr"))
# keep only the first 3 rows (header + 2 body) so the persistent reference stays small
KEEP = 3
total = len(rows)
for extra in rows[KEEP:]:
    tbl.remove(extra)
tbl.set("rowCnt", str(KEEP))
sz = tbl.find(hp("sz"))
if sz is not None and sz.get("height"):
    sz.set("height", str(int(int(sz.get("height")) * KEEP / total)))
rows = tbl.findall(hp("tr"))
generic = ["구분", "항목", "내용"]
for ri, tr in enumerate(rows):
    for ci, tc in enumerate(tr.findall(hp("tc"))):
        _set_cell_text_overwrite(tc, generic[ci] if (ri == 0 and ci < len(generic)) else "")
# add caption with {{table_template}} token (before first <hp:tr>)
width = int(tbl.find(hp("sz")).get("width"))
cap = build_caption(width)
first_tr = tbl.find(hp("tr"))
first_tr.addprevious(cap)
# also blank the visible sibling caption paragraph "<표 Ⅰ-1> ..." if present
for p in s4.findall(hp("p")):
    if para_text(p).strip().startswith("<표 Ⅰ-1>"):
        set_para_text(p, "")

# rebuild section4 child order
set_para_text(sec_p, "")
sec_p.set("styleIDRef", "0")   # neutral (avoid stray heading auto-number)

instr_lines = [
    "■ 작성 안내 (아래 지침들은 write 시 자동 제거됩니다)",
    "제목 번호(Ⅰ./1./1))는 마크다운에 직접 입력하세요 — 이 서식은 자동번호가 "
    "아닙니다 (예: '# Ⅰ. 서론', '## 1. 추진 배경', '### 1) 세부 과제').",
    "글머리표: 마크다운 '- '는 서식 글머리 사다리(￭ > ⦁ > -)로 매핑됩니다.",
    "표는 아래 {{table_template}} 표의 하우스 서식을 따릅니다. "
    "마크다운 표(| a | b |)를 쓰면 그 서식으로 생성됩니다.",
]
new_kids = [sec_p]
new_kids += [make_para(src, line, INSTR_STYLE) for line in instr_lines]
new_kids.append(make_para(src, "{{body}}", "0"))
new_kids.append(table_p)

for c in children:
    s4.remove(c)
for k in new_kids:
    s4.append(k)
overrides["Contents/section4.xml"] = dump("Contents/section4.xml", s4)

# ===== section5 : 참고문헌 -> keep headers, blank examples, add instruction =====
s5 = load("Contents/section5.xml")
headers = {"참고문헌", "해외 문헌", "국내 문헌", "기타 자료", "통계 자료"}
done5 = False
for p in s5.findall(hp("p")):
    txt = para_text(p).strip()
    if txt in headers or not txt:
        continue
    if not done5:
        p.set("styleIDRef", INSTR_STYLE)
        set_para_text(p, "참고문헌: 분류(해외/국내/기타/통계)별로 출처를 작성하세요. "
                         "(write 시 제거됨)")
        done5 = True
    else:
        set_para_text(p, "")
overrides["Contents/section5.xml"] = dump("Contents/section5.xml", s5)

# ===== section6 : colophon -> slots =====
s6 = load("Contents/section6.xml")
exact6 = {
    "정책연구 2026-00": "정책연구 {{report_number}}",
    "제주 경제전망 2026": "{{title}}",
    ": 선순환과 악순환의 전환점": "{{subtitle}}",
}
prefix6 = [
    ("연구책임", "연구책임   {{lead_researcher}}"),
    ("공동연구", "공동연구   {{co_researcher}}"),
    ("발 행 일", "발 행 일    {{pub_date}}"),
    ("발 행 인", "발 행 인    {{publisher}}"),
    ("ISBN", "ISBN      {{isbn}}"),
]
for p in s6.findall(hp("p")):
    txt = para_text(p).strip()
    if txt in exact6:
        set_para_text(p, exact6[txt])
        continue
    for pre, val in prefix6:
        if txt.startswith(pre):
            set_para_text(p, val)
            break
overrides["Contents/section6.xml"] = dump("Contents/section6.xml", s6)

# ===== drop orphaned images (example charts no longer referenced) =====
referenced = set()
for name in zin.namelist():
    if name.startswith("Contents/section") or "masterpage" in name:
        xml = overrides.get(name)
        if xml is not None:
            xml = xml.decode("utf-8")
        else:
            xml = zin.read(name).decode("utf-8", "ignore")
        referenced |= set(re.findall(r'binaryItemIDRef="([^"]+)"', xml))
# map id -> BinData href from content.hpf
hpf = zin.read("Contents/content.hpf").decode("utf-8")
id_href = dict(re.findall(r'<opf:item id="([^"]+)" href="(BinData/[^"]+)"', hpf))
orphans = {iid: href for iid, href in id_href.items() if iid not in referenced}
drop = set(orphans.values())
if orphans:
    # strip orphan <opf:item> lines from the manifest
    for iid in orphans:
        pat = r'<opf:item id="' + re.escape(iid) + r'" href="[^"]*"[^>]*/>'
        hpf = re.sub(pat, "", hpf)
    overrides["Contents/content.hpf"] = hpf.encode("utf-8")

# ===== write, preserving container (skipping dropped parts) =====
with zipfile.ZipFile(SRC) as zsrc, zipfile.ZipFile(DST, "w") as zout:
    for info in zsrc.infolist():
        if info.filename in drop:
            continue
        data = overrides.get(info.filename, zsrc.read(info.filename))
        zout.writestr(info, data)
print("wrote", DST)
print("overrides:", sorted(overrides))
print("dropped orphan images:", sorted(orphans.items()))
