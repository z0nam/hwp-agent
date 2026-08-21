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


def _hp(tag: str) -> str:
    return f"{{{_HP}}}{tag}"


def _blank_para(p) -> None:
    """문단의 보이는 텍스트를 비운다 — 첫 <hp:t>만 빈 문자열, 나머지 제거. ctrl/secPr 유지."""
    ts = p.findall(".//" + _hp("t"))
    for i, t in enumerate(ts):
        if i == 0:
            t.text = ""
        else:
            t.getparent().remove(t)
    p.set("styleIDRef", "0")


def _make_marker(opener):
    """opener 를 복제해 secPr/ctrl 런을 떼고 텍스트를 ``{{body}}`` 로 만든 마커 문단."""
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
    etree.SubElement(run, _hp("t")).text = "{{body}}"
    m.set("styleIDRef", "0")
    return m


def insert_body_marker(
    path: Path, section_index: int, position: str = "start", strip: bool = False
) -> None:
    """굽는 사본의 본문 섹션을 ``{{body}}`` 마커로 정리(컨테이너 보존).

    write 가 본문을 이 자리에 넣게 한다 — 마커가 없으면 마지막 섹션(판권지)에 붙음.
    *strip* 이면 그 섹션의 예시 문단을 걷어내고 [빈 opener][{{body}}] 만 남긴다
    (표지·판권·목차 등 다른 섹션은 무손상). *strip* 이 아니면 마커만 끼워 넣는다.
    """
    from lxml import etree

    from hwp_agent.ops.container import _rewrite_zip_preserving

    part = f"Contents/section{section_index}.xml"
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if part not in names:
            secs = sorted(n for n in names if re.search(r"section\d+\.xml$", n))
            part = secs[section_index]
        xml = z.read(part)
    root = etree.fromstring(xml)
    paras = root.findall(_hp("p"))

    if strip:
        # 본문 예시 제거: opener(secPr) 만 남겨 비우고, 그 뒤에 {{body}} 마커.
        opener = next((p for p in paras if p.find(".//" + _hp("secPr")) is not None), paras[0])
        marker = _make_marker(opener)
        _blank_para(opener)
        for p in paras:
            if p is not opener:
                root.remove(p)
        opener.addnext(marker)
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
            raise RuntimeError(f"{part}: {{body}} 마커로 복제할 단순 문단이 없음")
        marker = copy.deepcopy(src)
        marker.set("styleIDRef", "0")
        runs = marker.findall(_hp("run"))
        for r in runs[1:]:
            marker.remove(r)
        for child in list(runs[0]):
            runs[0].remove(child)
        etree.SubElement(runs[0], _hp("t")).text = "{{body}}"
        if position == "end":
            paras[-1].addnext(marker)
        else:
            opener = next((p for p in paras if p.find(".//" + _hp("secPr")) is not None), None)
            if opener is not None:
                opener.addnext(marker)
            else:
                paras[0].addprevious(marker)

    decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    new = decl + etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    tmp = path.with_suffix(".mark.tmp")
    _rewrite_zip_preserving(path, tmp, {part: new})
    tmp.replace(path)


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

    body = config.get("body")
    if body and body.get("section") is not None:
        insert_body_marker(
            out,
            int(body["section"]),
            body.get("position", "start"),
            strip=bool(body.get("strip")),
        )
        summary["body_marker"] = (
            f"section{body['section']}" + (" strip" if body.get("strip") else "")
        )
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
                + (f", {summ['body_marker']}에 {{body}}" if summ.get("body_marker") else "")
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
