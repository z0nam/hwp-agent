#!/usr/bin/env python3
"""전략기획실 보고서 서식(공용드라이브)을 개정될 때마다 기계친화 초벌구이.

전략기획실이 NAVER WORKS 공용드라이브 ``0.서식(과제 관련)`` 에 올리는 유형별 서식은
flat 스타일(번호·불릿에 outline 선언 없음)이라 ``hwp-agent write`` 가 그대로는 못 쓴다.
이 루틴은 하루 한 번(launchd) 돌며:

  1. 소스 폴더의 대상 파일들을 content-hash 로 스캔 → 개정/신규만 골라
  2. (.hwp 면 convert →) ``normalize`` 로 사다리(AI:HEADING/BULLET)를 선언한 사본을 굽고
  3. ``examples/house-forms/<이름>.normalized.hwpx`` 로 커밋 (격리된 worktree, 단일 auto 브랜치+PR)
  4. 본인 Slack 으로 요약 DM

**소스는 절대 수정하지 않는다** (읽기 전용, 사본에만 작업). 자동 커밋은 PR 로만 —
main 직접 push 안 함. 개정본은 한글 육안검증 후 사람이 머지한다 (normalize 산출물
게이트). 변경이 없으면 조용히 종료한다.

수동 실행:
    python scripts/refresh-house-forms.py            # 정식 (감지→굽기→PR→DM)
    python scripts/refresh-house-forms.py --dry-run  # 감지·굽기만, git/slack 없이
    python scripts/refresh-house-forms.py --force     # 해시 무시하고 전부 다시
    python scripts/refresh-house-forms.py --no-git --no-slack
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

# ── 설정 ────────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parents[1]
# 소스는 로컬 CloudStorage 마운트가 아니라 NAVER WORKS Drive API(`works` CLI)로
# 가져온다 — launchd 백그라운드는 FileProvider 마운트를 못 서비스해 open()이 무한
# 대기하기 때문. works 는 서비스계정 위임 API 라 헤드리스·이식 가능.
WORKS_BIN = "works"  # PATH(~/.local/bin/works)
WORKS_SD = "@2001000000544029"  # 공유드라이브 "0.서식(과제 관련)"
# 폴더 "연구보고서(유형별) 서식 및 보도자료 서식"
WORKS_FOLDER_ID = "QDIwMDEwMDAwMDA1NDQwMjl8MzQ3MjYxMzYwNjcyMzY3NDEyMHxEfDA"
# 대상 서식 (인용표기방법.hwp 는 채우는 서식이 아니라 제외). 개정되면 여기 갱신.
TARGETS = [
    "기반과제_서식.hwpx",
    "센터과제_서식.hwpx",
    "전략과제_서식.hwpx",
    "정책과제_서식.hwpx",
    "연구과제 이력카드 서식(성함,과제종류,과제명).hwp",
    "정책이슈브리프.hwp",
]
OUTPUT_SUBDIR = "examples/house-forms"  # 레포 내 산출물 위치
# per-form 명시 매핑(styleName→ROLE). 전략실은 스타일을 의미가 아니라 겉모양으로 매겨
# 이름 기반 자동추론이 불안정 — 렌더를 눈으로 보고 만든 매핑을 여기 두면 확정 적용된다.
# 파일명: overrides/<원본파일명>.json  (예: overrides/정책이슈브리프.hwp.json)
OVERRIDE_DIR = REPO / "overrides"
STATE_PATH = Path.home() / ".local/share/hwp-agent/house-forms.state.json"
LOG_PATH = Path.home() / ".local/share/hwp-agent/refresh-forms.log"
AUTO_BRANCH = "auto/house-forms-refresh"
# Slack: gw 의 봇토큰을 재사용 (새 시크릿 안 만듦). 우선순위대로 env 파일 탐색.
SLACK_ENV_CANDIDATES = [
    Path.home() / ".config/hwp-agent/refresh-forms.env",
    Path.home() / ".config/ji-gw-ai/.env",
]
NOTIFY_EMAIL = "namun@ji.re.kr"  # JI Slack DM 대상


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _works(args: list[str]) -> str:
    r = subprocess.run(
        [WORKS_BIN, *args], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"works {' '.join(args)} 실패: {r.stderr.strip()}")
    return r.stdout


def list_sources() -> dict[str, dict]:
    """WORKS 폴더의 파일 → {fileName: {fileId, modifiedTime}}. 개정 시그널=modifiedTime."""
    data = json.loads(_works(["ls", "--sd", WORKS_SD, WORKS_FOLDER_ID, "--json"]))
    files = data if isinstance(data, list) else data.get("files", [])
    return {
        f["fileName"]: {"fileId": f["fileId"], "modifiedTime": f["modifiedTime"]}
        for f in files
        if f.get("fileType") not in ("FOLDER",)
    }


def download_source(file_id: str, dest: Path) -> None:
    _works(["download", "--sd", WORKS_SD, "-o", str(dest), file_id])


def load_config(name: str) -> dict:
    """overrides/<name>.json 전체. {"styles": {name→ROLE}, "body": {"section": N}}."""
    f = OVERRIDE_DIR / f"{name}.json"
    if not f.is_file():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_HC = "http://www.hancom.co.kr/hwpml/2011/core"


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


def _hh(tag: str) -> str:
    return f"{{{_HH}}}{tag}"


def _hc(tag: str) -> str:
    return f"{{{_HC}}}{tag}"


def _blank_para(p) -> None:
    """문단의 보이는 텍스트를 비운다 — 첫 <hp:t>만 빈 문자열, 나머지 제거. ctrl/secPr 유지."""
    ts = p.findall(".//" + _hp("t"))
    for i, t in enumerate(ts):
        if i == 0:
            t.text = ""
        else:
            t.getparent().remove(t)
    p.set("styleIDRef", "0")


def _make_marker(opener, token: str = "{{body}}"):
    """opener 를 복제해 secPr/ctrl 런을 떼고 텍스트를 *token* 으로 만든 마커 문단."""
    from lxml import etree

    m = copy.deepcopy(opener)
    for run in m.findall(_hp("run")):
        if run.find(_hp("secPr")) is not None or run.find(_hp("ctrl")) is not None:
            m.remove(run)
    runs = m.findall(_hp("run"))
    if runs:
        run = runs[0]
        for r in runs[1:]:
            m.remove(r)
        for c in list(run):
            run.remove(c)
    else:
        run = etree.SubElement(m, _hp("run"))
    etree.SubElement(run, _hp("t")).text = token
    m.set("styleIDRef", "0")
    return m


def _para_text(p) -> str:
    return "".join((t.text or "") for t in p.findall(".//" + _hp("t")))


def _find_style(
    header_root, names: tuple[str, ...]
) -> tuple[str, str | None, str | None] | None:
    """header.xml 에서 *names* 중 첫 일치 스타일의 (styleID, paraPrID, charPrID) 반환."""
    if header_root is None:
        return None
    by_name = {
        (s.get("name") or "").strip(): (
            s.get("id"),
            s.get("paraPrIDRef"),
            s.get("charPrIDRef"),
        )
        for s in header_root.findall(".//" + _hh("style"))
        if s.get("id") is not None
    }
    for name in names:
        if name in by_name:
            return by_name[name]
    return None


def _find_borderless_bf(header_root) -> str | None:
    """네 변 모두 NONE 이고 배경 채움이 없는 borderFill id (주/출처 병합행용). 17 우선."""
    if header_root is None:
        return None
    sides = ("leftBorder", "rightBorder", "topBorder", "bottomBorder")
    matches: list[str] = []
    for bf in header_root.findall(".//" + _hh("borderFill")):
        bid = bf.get("id")
        if not bid:
            continue
        if not all(
            (b := bf.find(_hh(s))) is not None and b.get("type") == "NONE"
            for s in sides
        ):
            continue
        wb = bf.find(".//" + _hc("winBrush"))
        if wb is not None and (wb.get("faceColor") or "none").lower() != "none":
            continue
        matches.append(bid)
    if "17" in matches:
        return "17"
    return matches[-1] if matches else None


def _sibling_note_style(
    table_p, header_root=None
) -> tuple[str, str | None, str | None] | None:
    """표 바로 다음 문단이 주)/자료)/출처) 줄이면 그 문단의 (styleID, paraPrID, charPrID)."""
    from hwp_agent.ops.author import _NOTE_RE

    nxt = table_p.getnext()
    if nxt is None or nxt.tag != _hp("p") or nxt.find(".//" + _hp("tbl")) is not None:
        return None
    txt = _para_text(nxt).strip()
    sid = nxt.get("styleIDRef")
    note_names = {"자료", "JRI_각주", "각주1", "각주"}
    style_name = ""
    if header_root is not None and sid is not None:
        st = header_root.find(f".//{_hh('style')}[@id='{sid}']")
        style_name = (st.get("name") or "").strip() if st is not None else ""
    if not (_NOTE_RE.match(txt) or style_name in note_names):
        return None
    run = nxt.find(_hp("run"))
    cid = run.get("charPrIDRef") if run is not None else None
    return (sid or "0", nxt.get("paraPrIDRef"), cid)


def _pick_reference_table(paras: list, header_root=None):
    """섹션 문단들에서 ``{{table_template}}`` 기준으로 삼을 데이터 표 하나를 고른다.

    선택 규칙 (우선순위 순):
      1. 이미 캡션에 ``{{table…}}`` 토큰이 있는 표
      2. 주/출처 행(표 내부 하단 병합행 또는 바로 뒤 ``주)``/``자료)`` 문단)이 있는 표
      3. 머리·본문·끝행 대역이 모두 갖춰진 표(행 4개 이상)
      4. 문서 순서상 첫 번째 데이터 표(2행×2열 이상, 그림 포함 표 제외)
    """
    from hwp_agent.ops.author import (
        _TABLE_TOKEN_RE,
        _caption_text,
        _is_header_row,
        _table_format,
    )

    best = None
    best_rank = None
    for idx, p in enumerate(paras):
        tbl = p.find(".//" + _hp("tbl"))
        if tbl is None or tbl.find(".//" + _hp("pic")) is not None:
            continue
        rows = tbl.findall(_hp("tr"))
        if len(rows) < 2:
            continue
        logical_cols = int(tbl.get("colCnt", "0") or 0)
        if not logical_cols:
            logical_cols = max(
                (
                    sum(
                        int(span.get("colSpan", "1") or 1)
                        if (span := tc.find(_hp("cellSpan"))) is not None
                        else 1
                        for tc in tr.findall(_hp("tc"))
                    )
                    for tr in rows
                ),
                default=0,
            )
        if logical_cols < 2:
            continue

        h = 0
        while h < len(rows) and _is_header_row(rows[h]):
            h += 1
        h = h or 1
        # 헤더 행만 있고 본문 행이 없는 표(예: 2행 모두 헤더)는 기준 표에서 배제
        if len(rows) <= h:
            continue

        sib_note = _sibling_note_style(p, header_root)
        has_note = (_table_format(tbl).note is not None) or (sib_note is not None)
        has_existing_note = _table_format(tbl).note is not None and len(rows[h:]) > 1
        data_rows = rows[:-1] if has_existing_note else rows

        has_vmerge = any(
            int(span.get("rowSpan", "1") or 1) > 1
            for tc in tbl.findall(".//" + _hp("tc"))
            if (span := tc.find(_hp("cellSpan"))) is not None
        )
        has_hmerge = any(
            int(span.get("colSpan", "1") or 1) > 1
            for tr in data_rows
            for tc in tr.findall(_hp("tc"))
            if (span := tc.find(_hp("cellSpan"))) is not None
        )
        # 수직 병합이나 데이터 행의 수평 병합이 있는 표는 테두리 대역(border zones)을
        # 온전히 복원할 수 없으므로(중간선/상하단선 왜곡), designated 여부와 관계없이
        # 기준 표에서 완전히 배제(reject)한다.
        if has_vmerge or has_hmerge:
            continue

        designated = bool(_TABLE_TOKEN_RE.search(_caption_text(tbl)))
        full_bands = len(rows) >= 4
        rank = (designated, has_note, full_bands, -idx)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = (p, tbl, sib_note)
    return best


def _build_template_caption(
    width: str, style_info: tuple[str, str | None, str | None] | None
):
    """``<표 Ⅰ-1> {{table_template}}`` 자동번호 캡션 엘리먼트 생성."""
    from lxml import etree

    sid, pid, cid = style_info or ("0", "0", "0")
    xml = (
        f'<hp:caption xmlns:hp="{_HP}" side="TOP" fullSz="0"'
        f' width="{width}" gap="850" lastWidth="{width}">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK"'
        ' vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0"'
        ' textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        f'<hp:p id="2147483648" paraPrIDRef="{pid or "0"}" styleIDRef="{sid or "0"}"'
        ' pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{cid or "0"}">'
        "<hp:t>&lt;표 Ⅰ-</hp:t>"
        '<hp:ctrl><hp:autoNum num="1" numType="TABLE">'
        '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar=""'
        ' suffixChar="" supscript="0"/>'
        "</hp:autoNum></hp:ctrl>"
        "<hp:t>&gt; {{table_template}}</hp:t>"
        "</hp:run></hp:p></hp:subList></hp:caption>"
    )
    return etree.fromstring(xml.encode("utf-8"))


def _neutralize_reference_table(
    table_p, tbl, header_root=None, sibling_note_style=None
) -> None:
    """선택한 기준 표의 예시 내용을 비우고 ``{{table_template}}`` 캡션과 대역을 갖춘다.

    ``_table_format`` 이 머리행(header) · 첫 본문행(first_body) · 내부 본문행(body) ·
    마지막 본문행(last, 굵은 하단선) · 주/출처 병합행(note, 테두리 없음) 5개 대역을 모두
    온전히 읽도록 데이터 행은 ``[header, first_body, body, last]`` 4행을 남기고, 표에
    주/출처 행이 없으면 서식의 ``자료``/``각주`` 스타일과 무테두리 ``borderFill`` 로
    하단 병합행을 추가한다.
    """
    from hwp_agent.ops.author import (
        _TABLE_TOKEN_RE,
        _caption_text,
        _fit_table_height,
        _is_header_row,
        _table_format,
    )
    from hwp_agent.ops.form import _set_cell_text_overwrite

    has_vmerge = any(
        int(span.get("rowSpan", "1") or 1) > 1
        for tc in tbl.findall(".//" + _hp("tc"))
        if (span := tc.find(_hp("cellSpan"))) is not None
    )
    if has_vmerge:
        raise ValueError("Cannot neutralize reference table with vertical merges")

    caption_style = _find_style(header_root, ("표제목", "표/그림 제목"))
    body_cell_style = _find_style(header_root, ("표내용", "JRI_표내용"))
    note_style = sibling_note_style or _find_style(
        header_root, ("자료", "JRI_각주", "각주1", "각주")
    )
    borderless_bf = _find_borderless_bf(header_root)

    rows = tbl.findall(_hp("tr"))
    h = 0
    while h < len(rows) and _is_header_row(rows[h]):
        h += 1
    h = h or 1
    header_rows = rows[:h]
    rest = rows[h:]

    has_existing_note = _table_format(tbl).note is not None and len(rest) > 1
    existing_note = [rest[-1]] if has_existing_note else []
    data_body = rest[:-1] if has_existing_note else rest

    has_hmerge = any(
        int(span.get("colSpan", "1") or 1) > 1
        for tr in (*header_rows, *data_body)
        for tc in tr.findall(_hp("tc"))
        if (span := tc.find(_hp("cellSpan"))) is not None
    )
    if has_hmerge:
        raise ValueError(
            "Cannot neutralize reference table with horizontal merges in data rows"
        )

    # 논리 열 수: 테이블의 colCnt 속성 또는 각 행의 colSpan 합산 최댓값
    logical_cols = int(tbl.get("colCnt", "0") or 0)
    if not logical_cols:
        for tr in rows:
            r_cols = sum(
                int(span.get("colSpan", "1") or 1) if (span := tc.find(_hp("cellSpan"))) is not None else 1
                for tc in tr.findall(_hp("tc"))
            )
            logical_cols = max(logical_cols, r_cols)
    logical_cols = max(logical_cols, 1)

    if len(data_body) >= 3:
        # first_body(머리행 이중선 짝) + interior body(가는선) + last(굵은 하단선) 보존
        kept_body = [data_body[0], data_body[1], data_body[-1]]
    else:
        kept_body = list(data_body)

    keep_ids = {id(r) for r in (*header_rows, *kept_body, *existing_note)}
    for r in rows:
        if id(r) not in keep_ids:
            tbl.remove(r)

    generic = ["구분", "항목", "내용"]
    for ri, tr in enumerate(header_rows):
        for ci, tc in enumerate(tr.findall(_hp("tc"))):
            tc.set("header", "1")
            _set_cell_text_overwrite(
                tc, generic[ci] if (ri == 0 and ci < len(generic)) else ""
            )
            for lsa in tc.findall(".//" + _hp("linesegarray")):
                lsa.getparent().remove(lsa)

    for tr in kept_body:
        for tc in tr.findall(_hp("tc")):
            tc.set("header", "0")
            _set_cell_text_overwrite(tc, "")
            if body_cell_style is not None:
                b_sid, b_pid, b_cid = body_cell_style
                for p in tc.findall(_hp("subList") + "/" + _hp("p")):
                    if b_sid:
                        p.set("styleIDRef", b_sid)
                    if b_pid:
                        p.set("paraPrIDRef", b_pid)
                    for run in p.findall(_hp("run")):
                        if b_cid:
                            run.set("charPrIDRef", b_cid)
            sz = tc.find(_hp("cellSz"))
            if (
                sz is not None
                and (sz.get("height") or "").isdigit()
                and int(sz.get("height")) > 2183
            ):
                sz.set("height", "2183")
            for lsa in tc.findall(".//" + _hp("linesegarray")):
                lsa.getparent().remove(lsa)

    if existing_note:
        for tr in existing_note:
            for tc in tr.findall(_hp("tc")):
                tc.set("header", "0")
                _set_cell_text_overwrite(tc, "")
                sz = tc.find(_hp("cellSz"))
                if sz is not None:
                    sz.set("height", "0")
                for lsa in tc.findall(".//" + _hp("linesegarray")):
                    lsa.getparent().remove(lsa)
    elif note_style is not None and kept_body:
        note_tr = copy.deepcopy(kept_body[-1])
        tcs = note_tr.findall(_hp("tc"))
        tbl_w = int(tbl.find(_hp("sz")).get("width", "0") or 0) if tbl.find(_hp("sz")) is not None else 0
        sum_w = sum(
            int(s.get("width", "0"))
            for tc in tcs
            if (s := tc.find(_hp("cellSz"))) is not None
        )
        total_w = tbl_w or sum_w
        tc0 = tcs[0]
        for extra in tcs[1:]:
            note_tr.remove(extra)
        tc0.set("header", "0")
        if borderless_bf:
            tc0.set("borderFillIDRef", borderless_bf)
        span = tc0.find(_hp("cellSpan"))
        if span is not None:
            span.set("colSpan", str(logical_cols))
            span.set("rowSpan", "1")
        sz = tc0.find(_hp("cellSz"))
        if sz is not None:
            if total_w:
                sz.set("width", str(total_w))
            sz.set("height", "0")
        sub = tc0.find(_hp("subList"))
        if sub is not None:
            sub.set("vertAlign", "TOP")
        _set_cell_text_overwrite(tc0, "")
        n_sid, n_pid, n_cid = note_style
        for p in tc0.findall(_hp("subList") + "/" + _hp("p")):
            if n_sid:
                p.set("styleIDRef", n_sid)
            if n_pid:
                p.set("paraPrIDRef", n_pid)
            for run in p.findall(_hp("run")):
                if n_cid:
                    run.set("charPrIDRef", n_cid)
        for lsa in tc0.findall(".//" + _hp("linesegarray")):
            lsa.getparent().remove(lsa)
        tbl.append(note_tr)

    final_rows = tbl.findall(_hp("tr"))
    tbl.set("rowCnt", str(len(final_rows)))
    for ri, tr in enumerate(final_rows):
        col_cursor = 0
        for tc in tr.findall(_hp("tc")):
            addr = tc.find(_hp("cellAddr"))
            if addr is not None:
                addr.set("rowAddr", str(ri))
                addr.set("colAddr", str(col_cursor))
            span = tc.find(_hp("cellSpan"))
            c_span = int(span.get("colSpan", "1") or 1) if span is not None else 1
            col_cursor += c_span

    cap = tbl.find(_hp("caption"))
    if cap is not None:
        if not _TABLE_TOKEN_RE.search(_caption_text(tbl)):
            ts = cap.findall(".//" + _hp("t"))
            if ts:
                last = ts[-1].text or ""
                m = re.match(r"^([^\]>]*[>\]])", last)
                framing = m.group(1) if m else last.rstrip()
                ts[-1].text = f"{framing} {{{{table_template}}}}".strip()
        for lsa in cap.findall(".//" + _hp("linesegarray")):
            lsa.getparent().remove(lsa)
    else:
        tbl_sz = tbl.find(_hp("sz"))
        width = tbl_sz.get("width", "36805") if tbl_sz is not None else "36805"
        cap = _build_template_caption(width, caption_style)
        if final_rows:
            final_rows[0].addprevious(cap)
        else:
            tbl.append(cap)

    _fit_table_height(tbl)
    table_p.set("styleIDRef", "0")
    for run in table_p.findall(_hp("run")):
        if run.find(_hp("tbl")) is None:
            for t in run.findall(_hp("t")):
                t.text = ""
    for lsa in table_p.findall(_hp("linesegarray")):
         table_p.remove(lsa)


def insert_body_marker(
    path: Path,
    section_index: int,
    position: str = "start",
    strip: bool = False,
    marker: str = "body",
    keep_heading: bool = False,
    keep_table: bool = False,
) -> bool:
    """굽는 사본의 지정 섹션을 ``{{<marker>}}`` 마커로 정리(컨테이너 보존).

    write/build 가 그 파트를 이 자리에 넣게 한다 — 마커가 없으면 마지막 섹션(판권지)에
    붙음. *strip* 이면 그 섹션의 예시 문단을 걷어내고 [빈 opener][마커] 만 남긴다
    (표지·판권 등 다른 섹션은 무손상). *strip* 이 아니면 마커만 끼워 넣는다.
    *marker* 는 마커 이름(``body``·``intro``·``references`` …) — 토큰은 ``{{name}}``.

    *keep_heading* 이면 섹션의 고정 제목 문단을 **보존**하고 마커를 그 뒤에 둔다.
    참고문헌 제목처럼 authoring 이 재현 못하는 전용 글자스타일(예: charPr 26, 15pt)을
    유지하기 위함 — 이 경우 해당 파트 MD 는 제목(`# 참고문헌`)을 넣지 않는다. 제목이
    opener(secPr) 에 얹혀 있으면 opener 를, opener 가 빈 문단이면 그 뒤 첫 텍스트
    문단(제목)을 앵커로 삼는다.

    *keep_table* 이면 *strip* 시 섹션의 데이터 표 하나를 골라 내용을 중립화하고 캡션을
    ``{{table_template}}`` 로 달아 마커 뒤에 남긴다(기관 표 서식 보존). 보존된 표가
    있으면 ``True`` 반환.
    """
    token = "{{" + marker + "}}"
    from lxml import etree

    from hwp_agent.ops.container import _rewrite_zip_preserving

    part = f"Contents/section{section_index}.xml"
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if part not in names:
            secs = sorted(n for n in names if re.search(r"section\d+\.xml$", n))
            part = secs[section_index]
        xml = z.read(part)
        header_root = (
            etree.fromstring(z.read("Contents/header.xml"))
            if "Contents/header.xml" in names
            else None
        )
    root = etree.fromstring(xml)
    paras = root.findall(_hp("p"))
    preserved_table = False

    if strip:
        # 본문 예시 제거: opener(secPr) 만 남겨 비우고, 그 뒤에 {{body}} 마커.
        opener = next((p for p in paras if p.find(".//" + _hp("secPr")) is not None), paras[0])
        marker_el = _make_marker(opener, token)
        picked = _pick_reference_table(paras, header_root) if keep_table else None
        table_p = None
        if picked is not None:
            cand_p, cand_tbl, sib_note = picked
            if cand_p is opener:
                table_p = copy.deepcopy(cand_p)
                for run in list(table_p.findall(_hp("run"))):
                    if run.find(_hp("secPr")) is not None:
                        table_p.remove(run)
                for run in list(opener.findall(_hp("run"))):
                    if run.find(_hp("tbl")) is not None:
                        opener.remove(run)
                cand_tbl = table_p.find(".//" + _hp("tbl"))
            else:
                table_p = cand_p
            _neutralize_reference_table(table_p, cand_tbl, header_root, sib_note)
            preserved_table = True

        if keep_heading:
            # 고정 제목을 살린다: opener(제목이 얹힌 경우) 또는 opener 가 비어 있으면
            # 뒤 첫 텍스트 문단(제목)까지 남기고, 나머지 예시만 제거. 마커는 앵커 뒤.
            keep = {opener}
            anchor = opener
            if not _para_text(opener).strip():
                title = next(
                    (p for p in paras if p is not opener and _para_text(p).strip()),
                    None,
                )
                if title is not None:
                    keep.add(title)
                    anchor = title
            for p in paras:
                if p not in keep:
                    root.remove(p)
            anchor.addnext(marker_el)
        else:
            _blank_para(opener)
            for p in paras:
                if p is not opener:
                    root.remove(p)
            opener.addnext(marker_el)
        if table_p is not None:
            marker_el.addnext(table_p)
    else:
        src = next(
            (
                p
                for p in paras
                if p.find(_hp("run") + "/" + _hp("t")) is not None
                and p.find(".//" + _hp("secPr")) is None
                and p.find(".//" + _hp("tbl")) is None
            ),
            None,
        )
        if src is None:
            raise RuntimeError(f"{part}: 마커로 복제할 단순 문단이 없음")
        marker_el = copy.deepcopy(src)
        marker_el.set("styleIDRef", "0")
        runs = marker_el.findall(_hp("run"))
        for r in runs[1:]:
            marker_el.remove(r)
        for child in list(runs[0]):
            runs[0].remove(child)
        etree.SubElement(runs[0], _hp("t")).text = token
        if position == "end":
            paras[-1].addnext(marker_el)
        else:
            opener = next((p for p in paras if p.find(".//" + _hp("secPr")) is not None), None)
            if opener is not None:
                opener.addnext(marker_el)
            else:
                paras[0].addprevious(marker_el)

    decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    new = decl + etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    tmp = path.with_suffix(".mark.tmp")
    _rewrite_zip_preserving(path, tmp, {part: new})
    tmp.replace(path)
    return preserved_table


def load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log(f"⚠ 상태파일 손상, 새로 시작: {STATE_PATH}")
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 초벌구이 (convert + normalize) ──────────────────────────────────────────
def bake_one(src: Path, out: Path, workdir: Path, config: dict | None = None) -> dict:
    """src 를 기계친화 사본 out 으로. 요약 dict 반환.

    config["styles"] (styleName→ROLE) 가 있으면 자동추론 대신 명시적 매핑을 적용
    (스타일 이름이 번호 모양을 안 담는 서식/명명 변경 복구 경로). config["body"]
    ({"section": N}) 가 있으면 굽는 사본에 ``{{body}}`` 마커를 삽입해 write 의 본문
    삽입 위치를 지정한다(없으면 마지막 섹션=판권지 등에 붙음).
    """
    from hwp_agent.ops import (
        apply_normalization,
        apply_style_roles,
        classify_document,
        plan_normalization,
    )

    config = config or {}
    styles_override = config.get("styles")

    # .hwp → .hwpx 먼저
    if src.suffix.lower() == ".hwp":
        from hwp_agent.convert import Hwp2HwpxBackend

        backend = Hwp2HwpxBackend()
        if not backend.is_available():
            raise RuntimeError(
                f"변환기 jar/Java 없음 — .hwp 처리 불가: {src.name}"
            )
        hwpx = workdir / (src.stem + ".hwpx")
        backend.convert(src, hwpx)
        target = hwpx
    else:
        target = src

    out.parent.mkdir(parents=True, exist_ok=True)

    if styles_override:
        # 명시적 매핑(override): 자동추론 우회, 지정 스타일에 engName 확정 선언.
        before = classify_document(target)
        actions = apply_style_roles(target, styles_override, out)  # 없는 스타일이면 raise
        summary = {
            "source": src.name,
            "classification_before": before,
            "classification_after": classify_document(out),
            "declarations": len(actions),
            "plan_warnings": [],
            "note": "명시적 매핑(override) 적용",
        }
    else:
        plan = plan_normalization(target)
        summary = {
            "source": src.name,
            "classification_before": plan.classification_before,
            "classification_after": plan.classification_expected,
            "declarations": len(plan.actions),
            # normalize가 사다리를 못 세운 이유(번호 겹침·OUTLINE 혼재 등) — 회귀 시 원인 표시.
            "plan_warnings": list(plan.warnings),
        }
        if plan.actions:
            apply_normalization(target, plan, out)
        else:
            # 선언할 게 없으면 (이미 구조적이거나 사다리 없음) 변환본을 그대로 산출
            shutil.copyfile(target, out)
            summary["note"] = "선언 불필요 (이미 기계친화이거나 사다리 없음)"

    # marker insertion: a `sections` list (multi-marker) or the `body` shorthand.
    # Each spec: {"section": N, "marker": "body"|"intro"|"references"…, "strip": bool}.
    specs = config.get("sections")
    if not specs and config.get("body", {}).get("section") is not None:
        b = config["body"]
        specs = [{"section": b["section"], "marker": "body", **b}]
    placed_markers = []
    for spec in specs or []:
        if spec.get("section") is None:
            continue
        mk = spec.get("marker", "body")
        preserved = insert_body_marker(
            out,
            int(spec["section"]),
            spec.get("position", "start"),
            strip=bool(spec.get("strip")),
            marker=mk,
            keep_heading=bool(spec.get("keep_heading")),
            keep_table=bool(spec.get("keep_table", mk == "body")),
        )
        tag = ("·strip" if spec.get("strip") else "") + ("·tbl" if preserved else "")
        placed_markers.append(f"s{spec['section']}:{{{{{mk}}}}}{tag}")
    if placed_markers:
        summary["body_marker"] = " ".join(placed_markers)
    return summary


# ── Slack DM ────────────────────────────────────────────────────────────────
def _load_slack_token() -> str | None:
    for env_path in SLACK_ENV_CANDIDATES:
        if not env_path.is_file():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("SLACK_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _slack_api(token: str, method: str, payload: dict, *, get: bool = False) -> dict:
    # users.lookupByEmail 등은 GET+urlencoded 를 요구 (JSON body 는 무시됨).
    if get:
        url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(payload)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    else:
        req = urllib.request.Request(
            f"https://slack.com/api/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def slack_dm(text: str) -> None:
    token = _load_slack_token()
    if not token:
        log("⚠ SLACK_BOT_TOKEN 없음 — DM 생략")
        return
    try:
        r = _slack_api(token, "users.lookupByEmail", {"email": NOTIFY_EMAIL}, get=True)
        if not r.get("ok"):
            log(f"⚠ Slack lookup 실패: {r.get('error')}")
            return
        uid = r["user"]["id"]
        r = _slack_api(token, "conversations.open", {"users": uid})
        if not r.get("ok"):
            log(f"⚠ Slack conversations.open 실패: {r.get('error')}")
            return
        channel = r["channel"]["id"]
        r = _slack_api(token, "chat.postMessage", {"channel": channel, "text": text})
        if not r.get("ok"):
            log(f"⚠ Slack postMessage 실패: {r.get('error')}")
        else:
            log("Slack DM 전송됨")
    except (urllib.error.URLError, KeyError, TimeoutError) as e:
        log(f"⚠ Slack DM 예외(무시): {e}")


# ── git / PR (격리 worktree) ────────────────────────────────────────────────
def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def publish_pr(baked: dict[str, Path], summaries: list[dict]) -> str | None:
    """산출물을 격리 worktree 에서 auto 브랜치로 커밋+push, PR 보장. PR url 반환."""
    _git(["fetch", "origin", "main"], REPO)
    scratch = Path(tempfile.mkdtemp(prefix="house-forms-wt-"))
    wt = scratch / "wt"
    try:
        # origin/main 기준 새 worktree + 브랜치 (기존 auto 브랜치 덮어씀)
        _git(
            ["worktree", "add", "--force", "-B", AUTO_BRANCH, str(wt), "origin/main"],
            REPO,
        )
        dest_dir = wt / OUTPUT_SUBDIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        for out in baked.values():
            shutil.copyfile(out, dest_dir / out.name)
        _git(["add", OUTPUT_SUBDIR], wt)
        # 변경 없으면(내용 동일) 커밋 건너뜀
        if _git(["status", "--porcelain"], wt).stdout.strip() == "":
            log("worktree 에 실질 변경 없음 — 커밋/PR 생략")
            return None
        def _tag(s: dict) -> str:
            if s.get("regressed"):
                return "⚠️ 회귀 "
            return "🆕 " if s.get("changed") else ""
        lines = "\n".join(
            f"- {_tag(s)}{s['source']}: "
            f"{s['classification_before']} → {s['classification_after']} "
            f"(선언 {s['declarations']}건)"
            + (f" — {s['note']}" if s.get("note") else "")
            + ("".join(f"\n  ↳ {w}" for w in s.get("plan_warnings", []))
               if s.get("regressed") else "")
            for s in summaries
        )
        regressed = [s["source"] for s in summaries if s.get("regressed")]
        banner = (
            f"⚠️ 회귀 {len(regressed)}건 — 이전엔 기계친화였는데 이번 개정에서 떨어짐 "
            "(스타일 명명 변경 의심). 머지 전 원인 확인·매핑 보정 필요.\n\n"
            if regressed else ""
        )
        msg = (
            "chore(house-forms): 서식 개정 초벌구이 (자동)\n\n"
            "전략기획실 공용드라이브 서식을 normalize 로 기계친화화.\n"
            "한글 육안검증(보안경고·레이아웃·F6 영문이름) 후 머지.\n\n"
            f"{banner}{lines}\n\n"
            "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>\n"
        )
        _git(["commit", "-m", msg], wt)
        _git(["push", "--force", "origin", AUTO_BRANCH], wt)
        # PR 보장 (없으면 생성)
        existing = subprocess.run(
            ["gh", "pr", "list", "--head", AUTO_BRANCH, "--state", "open",
             "--json", "url", "--jq", ".[0].url"],
            cwd=wt, capture_output=True, text=True,
        ).stdout.strip()
        if existing:
            log(f"기존 PR 갱신됨: {existing}")
            return existing
        created = subprocess.run(
            ["gh", "pr", "create", "--base", "main", "--head", AUTO_BRANCH,
             "--title", "chore(house-forms): 서식 개정 초벌구이 (자동)",
             "--body",
             "전략기획실 서식 개정 자동 감지 → normalize 초벌구이.\n\n"
             f"{banner}{lines}\n\n"
             "**머지 전 한글 육안검증**: 보안경고 없음 · 레이아웃 정상 · "
             "스타일(F6) 영문이름에 AI:HEADING_n/AI:BULLET_n.\n\n"
             "🤖 Generated with [Claude Code](https://claude.com/claude-code)"],
            cwd=wt, capture_output=True, text=True,
        )
        url = created.stdout.strip()
        if created.returncode != 0:
            log(f"⚠ gh pr create 실패: {created.stderr.strip()}")
            return None
        log(f"PR 생성됨: {url}")
        return url
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO)
        shutil.rmtree(scratch, ignore_errors=True)


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="감지·굽기만 (git/slack 없음)")
    ap.add_argument("--force", action="store_true", help="해시 무시, 전부 다시 굽기")
    ap.add_argument("--no-git", action="store_true", help="PR 단계 생략")
    ap.add_argument("--no-slack", action="store_true", help="Slack DM 생략")
    args = ap.parse_args()

    state = load_state()
    # 직전 확정값 스냅샷 (회귀 감지 기준). _pending 을 뒤에 붙여도 이 사본은 안 바뀜.
    baseline = {name: dict(state.get(name, {})) for name in TARGETS}
    try:
        sources = list_sources()
    except (RuntimeError, json.JSONDecodeError) as e:
        log(f"✗ WORKS 소스 목록 실패(인증/네트워크?): {e}")
        return 2

    changed: list[str] = []
    present: list[str] = []
    for name in TARGETS:
        meta = sources.get(name)
        if not meta:
            log(f"⚠ 대상 없음(WORKS 폴더에서 사라짐/이름 변경?): {name}")
            continue
        present.append(name)
        rev = meta["modifiedTime"]
        prev = state.get(name, {}).get("modifiedTime")
        if args.force or rev != prev:
            changed.append(name)
        state.setdefault(name, {})["_pending"] = {
            "modifiedTime": rev, "fileId": meta["fileId"]
        }

    if not changed:
        log("변경 없음 — 종료 (noop)")
        return 0

    # 무언가 바뀌었으면 PR 은 항상 전체 집합을 담는다 — 부분 재굽기가 나머지를
    # 떨구지 않도록(브랜치는 house-forms 없는 origin/main 기준이라, 이번 구운 것만
    # 올리면 나머지가 사라짐). 감지된 변경분(changed)은 보고 강조용으로만 구분.
    changed_set = set(changed)
    log(f"개정/신규 {len(changed)}건: {', '.join(changed)} "
        f"→ PR 완결성 위해 전체 {len(present)}종 재굽기")

    baked: dict[str, Path] = {}
    summaries: list[dict] = []
    out_root = Path(tempfile.mkdtemp(prefix="house-forms-out-"))
    workdir = out_root / "work"
    workdir.mkdir()
    for name in present:
        try:
            src = workdir / name  # WORKS 다운로드 로컬 사본 (suffix 유지)
            download_source(sources[name]["fileId"], src)
            out = out_root / (Path(name).stem + ".normalized.hwpx")
            summ = bake_one(src, out, workdir, config=load_config(name))
            summ["changed"] = name in changed_set
            baked[name] = out
            summaries.append(summ)
            log(f"구움{'🆕' if summ['changed'] else '·유지'}: {name} → {out.name}  "
                f"[{summ['classification_before']}→{summ['classification_after']}, "
                f"선언 {summ['declarations']}"
                + (f", 마커 {summ['body_marker']}" if summ.get("body_marker") else "")
                + "]")
        except Exception as e:  # noqa: BLE001 — 한 파일 실패가 전체를 막지 않게
            log(f"✗ 실패: {name}: {e}")
            traceback.print_exc()

    if not baked:
        log("✗ 구워진 산출물 없음 — 상태 미갱신, 종료")
        return 1

    # 회귀 감지: 직전에 기계친화(structured/선언≥1)였던 서식이 이번에 떨어졌으면
    # 스타일 명명이 바뀐 신호 — 조용히 넘기지 않고 크게 알린다.
    for summ in summaries:
        prev = baseline.get(summ["source"], {})
        prev_class = prev.get("classification")
        prev_decls = prev.get("declarations")
        summ["regressed"] = (
            prev_class == "structured"
            and summ["classification_after"] != "structured"
        ) or (
            isinstance(prev_decls, int)
            and prev_decls > 0
            and summ["declarations"] < prev_decls
        )
        if summ["regressed"]:
            log(f"⚠⚠ 회귀 감지: {summ['source']} — 직전 {prev_class}({prev_decls}선언) "
                f"→ 이번 {summ['classification_after']}({summ['declarations']}선언). "
                f"스타일 명명 변경 의심 — 수동 확인 필요. "
                f"{'; '.join(summ.get('plan_warnings', [])) or '(normalize 경고 없음)'}")

    if args.dry_run:
        log(f"[dry-run] 산출물 {len(baked)}건: {out_root}")
        for s in summaries:
            log(f"  {json.dumps(s, ensure_ascii=False)}")
        return 0

    pr_url = None
    if not args.no_git:
        try:
            pr_url = publish_pr(baked, summaries)
        except subprocess.CalledProcessError as e:
            log(f"✗ git 단계 실패: {e.cmd}\n{e.stderr}")
            return 1  # 상태 미갱신 → 다음 실행에서 재시도

    # 성공 → 상태 확정 (성공적으로 처리된 것만)
    stamp = datetime.now().isoformat(timespec="seconds")
    by_name = {s["source"]: s for s in summaries}
    for name in list(state.keys()):
        pend = state[name].pop("_pending", None)
        if name in baked and pend:
            s = by_name.get(name, {})
            state[name] = {
                **pend,
                "classification": s.get("classification_after"),
                "declarations": s.get("declarations"),
                "baked_at": stamp,
            }
    save_state(state)

    # Slack DM
    if not args.no_slack:
        def _prefix(s: dict) -> str:
            if s.get("regressed"):
                return "⚠️ "
            return "🆕 " if s.get("changed") else "· "
        lines = "\n".join(
            (f"{_prefix(s)}"
             f"{s['source']}: {s['classification_before']}→{s['classification_after']} "
             f"(선언 {s['declarations']})"
             + (f" — {s['note']}" if s.get("note") else "")
             + ("".join(f"\n    ↳ {w}" for w in s.get("plan_warnings", []))
                if s.get("regressed") else ""))
            for s in summaries
        )
        regressed = [s["source"] for s in summaries if s.get("regressed")]
        head = (
            f"🚨 *서식 회귀 감지 {len(regressed)}건* (스타일 명명 변경 의심 — 수동 확인) "
            f"· 변경 {len(changed)} / 전체 {len(baked)}종 PR 갱신"
            if regressed
            else f"📄 *서식 개정* — 변경 {len(changed)}건 · 전체 {len(baked)}종 PR 갱신"
        )
        text = head + f"\n{lines}\n" + (
            f"\nPR(한글 육안검증 후 머지): {pr_url}" if pr_url else ""
        )
        slack_dm(text)

    log("완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
