# 서식 초벌구이 자동화 — 공식 서식을 기계친화로 유지

전략기획실이 NAVER WORKS 공용드라이브 `0.서식(과제 관련)/연구보고서(유형별) …` 에
올리는 유형별 보고서 서식은 **개정될 때마다 그 폴더가 최신본**이지만, 번호·불릿에
outline 선언이 없는 **flat 스타일**이라 `hwp-agent write` 가 그대로는 못 먹는다.
개정될 때마다 사람이 손보는 대신, 이 루틴이 매일 새벽 한 번 돌며 **개정을 감지해
기계친화 초벌구이(normalize)** 를 하고 PR 로 올린다.

## 무엇을 하나

매일 **05:30**(launchd), 이 맥에서:

1. 소스 폴더의 대상 서식을 **content-hash** 로 스캔 → 개정/신규만 선별
2. (`.hwp` 면 `convert` →) **`normalize`** 로 `AI:HEADING_n`/`AI:BULLET_n` 사다리를
   선언한 사본을 굽는다 (한글 이름 보존, 컨테이너 보존)
3. `examples/house-forms/<이름>.normalized.hwpx` 로 커밋 — **격리된 git worktree**
   에서 단일 `auto/house-forms-refresh` 브랜치에 올리고 **PR 생성/갱신**
4. 본인 **Slack DM** 으로 요약 통지

변경이 없으면 조용히 종료한다.

### 설계 원칙

- **소스는 절대 수정 안 함.** 공유드라이브 원본은 읽기 전용, 언제나 사본에만 작업.
- **normalize 만 (초벌구이).** 견고한 스타일 기반 변환. 예시 본문을 걷어내는 완전
  golden template(슬롯·지침) 스켈레톤화는 표지/판권 문자열 매칭이 서식마다 달라
  개정 자동화에 취약하므로 **자동 범위에서 제외** — 필요할 때 사람이 수동 큐레이션
  (참고: [golden-template.md](golden-template.md)).
- **자동 커밋은 PR 로만, main 직접 push 안 함.** normalize 산출물은 한글에서
  **육안검증**(보안경고·레이아웃·F6 영문이름) 후 사람이 머지한다.
- **격리 worktree** 에서 git 작업 → 사용자의 라이브 체크아웃/작업 브랜치를 안 건드림.

## 대상 서식

`scripts/refresh-house-forms.py` 상단 `TARGETS` 에서 관리(개정으로 폴더/파일명이
바뀌면 여기 갱신):

| 파일 | 종류 | 초벌구이 결과 |
|------|------|---------------|
| 기반과제_서식.hwpx | 보고서 | flat → structured (선언 7) |
| 센터과제_서식.hwpx | 보고서 | flat → structured (선언 7) |
| 전략과제_서식.hwpx | 보고서 | flat → structured (선언 7) |
| 정책과제_서식.hwpx | 보고서 | flat → structured (선언 7) |
| 연구과제 이력카드 서식….hwp | 양식 | convert 만 (사다리 없음, 0선언) |
| 정책이슈브리프.hwp | 보고서 | convert 만 (0선언) |

`인용표기방법.hwp` 는 채우는 서식이 아니라 스타일 안내문이라 제외.

## 설치 / 운영

```bash
# 설치(재실행 안전) — 매일 05:30 잡 등록
scripts/install-refresh-forms.sh

# 즉시 1회 실행(테스트)
launchctl kickstart -k gui/$(id -u)/re.ji.hwp-agent.refresh-forms

# 수동 실행 (개발/디버그)
uv run python scripts/refresh-house-forms.py --dry-run   # 감지·굽기만
uv run python scripts/refresh-house-forms.py --force      # 해시 무시 전부 다시
uv run python scripts/refresh-house-forms.py --no-slack   # DM 없이

# 제거
launchctl bootout gui/$(id -u)/re.ji.hwp-agent.refresh-forms
rm ~/Library/LaunchAgents/re.ji.hwp-agent.refresh-forms.plist
```

- **상태파일**(개정 감지 기준): `~/.local/share/hwp-agent/house-forms.state.json`
  — 소스별 마지막 초벌구이 해시. 지우면 다음 실행에서 전부 다시 굽는다.
- **로그**: `~/.local/share/hwp-agent/refresh-forms.log` (+ launchd stdout
  `refresh-forms.launchd.log`).
- **Slack**: gw 의 봇토큰을 재사용한다(`SLACK_BOT_TOKEN`). 탐색 순서
  `~/.config/hwp-agent/refresh-forms.env` → `~/.config/ji-gw-ai/.env`. DM 대상은
  스크립트의 `NOTIFY_EMAIL`.

## 전제·한계

- **로그인 세션 전용**(LaunchAgent): git push(SSH 키)·`gh`(토큰)·NAVER WORKS
  마운트 모두 로그인 상태에서만 닿는다. 로그아웃/로그인 화면에서는 안 돈다.
- **이 맥 고정**: 공유드라이브가 이 맥의 CloudStorage 로컬 마운트라, 클라우드
  에이전트로는 못 옮긴다.
- **PR 이 안 머지된 채 소스가 또 개정되면** 같은 `auto/house-forms-refresh` 브랜치를
  force-push 로 갱신한다(PR 하나 유지). PR 을 머지 없이 닫으면 그 초벌구이는
  다음 소스 개정까지 재생성되지 않는다(상태파일이 이미 최신 해시라서). 필요하면
  상태파일에서 해당 항목을 지우고 재실행.
