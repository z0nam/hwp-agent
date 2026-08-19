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
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 설정 ────────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(
    "/Users/namun/Library/CloudStorage/NAVERWORKSDrive-namun@ji.re.kr"
    "/Collaborative Drive/0.서식(과제 관련)/연구보고서(유형별) 서식 및 보도자료 서식"
)
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


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
def bake_one(src: Path, out: Path, workdir: Path) -> dict:
    """src 를 기계친화 사본 out 으로. 요약 dict 반환."""
    from hwp_agent.ops import apply_normalization, plan_normalization

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

    plan = plan_normalization(target)
    summary = {
        "source": src.name,
        "classification_before": plan.classification_before,
        "classification_after": plan.classification_expected,
        "declarations": len(plan.actions),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    if plan.actions:
        apply_normalization(target, plan, out)
    else:
        # 선언할 게 없으면 (이미 구조적이거나 사다리 없음) 변환본을 그대로 산출
        shutil.copyfile(target, out)
        summary["note"] = "선언 불필요 (이미 기계친화이거나 사다리 없음)"
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
        lines = "\n".join(
            f"- {s['source']}: {s['classification_before']} → "
            f"{s['classification_after']} (선언 {s['declarations']}건)"
            + (f" — {s['note']}" if s.get("note") else "")
            for s in summaries
        )
        msg = (
            "chore(house-forms): 서식 개정 초벌구이 (자동)\n\n"
            "전략기획실 공용드라이브 서식을 normalize 로 기계친화화.\n"
            "한글 육안검증(보안경고·레이아웃·F6 영문이름) 후 머지.\n\n"
            f"{lines}\n\n"
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
             f"{lines}\n\n"
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

    if not SOURCE_DIR.is_dir():
        log(f"✗ 소스 폴더 접근 불가(마운트 안 됨?): {SOURCE_DIR}")
        return 2

    state = load_state()
    to_bake: list[Path] = []
    for name in TARGETS:
        src = SOURCE_DIR / name
        if not src.is_file():
            log(f"⚠ 대상 없음(폴더 구조 바뀜?): {name}")
            continue
        digest = sha256(src)
        prev = state.get(name, {}).get("sha256")
        if args.force or digest != prev:
            to_bake.append(src)
        state.setdefault(name, {})["_pending_sha256"] = digest

    if not to_bake:
        log("변경 없음 — 종료 (noop)")
        return 0

    log(f"개정/신규 {len(to_bake)}건: {', '.join(p.name for p in to_bake)}")

    baked: dict[str, Path] = {}
    summaries: list[dict] = []
    out_root = Path(tempfile.mkdtemp(prefix="house-forms-out-"))
    workdir = out_root / "work"
    workdir.mkdir()
    for src in to_bake:
        try:
            out = out_root / (src.stem + ".normalized.hwpx")
            summ = bake_one(src, out, workdir)
            baked[src.name] = out
            summaries.append(summ)
            log(f"구움: {src.name} → {out.name}  "
                f"[{summ['classification_before']}→{summ['classification_after']}, "
                f"선언 {summ['declarations']}]")
        except Exception as e:  # noqa: BLE001 — 한 파일 실패가 전체를 막지 않게
            log(f"✗ 실패: {src.name}: {e}")
            traceback.print_exc()

    if not baked:
        log("✗ 구워진 산출물 없음 — 상태 미갱신, 종료")
        return 1

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
    for name in list(state.keys()):
        pend = state[name].pop("_pending_sha256", None)
        if name in baked and pend:
            state[name] = {"sha256": pend, "baked_at": stamp}
    save_state(state)

    # Slack DM
    if not args.no_slack:
        lines = "\n".join(
            f"• {s['source']}: {s['classification_before']}→{s['classification_after']} "
            f"(선언 {s['declarations']})" + (f" — {s['note']}" if s.get("note") else "")
            for s in summaries
        )
        text = (
            f"📄 *서식 개정 초벌구이* — {len(baked)}건 처리\n{lines}\n"
            + (f"\nPR(한글 육안검증 후 머지): {pr_url}" if pr_url else "")
        )
        slack_dm(text)

    log("완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
