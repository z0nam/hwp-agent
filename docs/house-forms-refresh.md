# 서식 초벌구이 자동화 — 공식 서식을 기계친화로 유지

전략기획실이 NAVER WORKS 공용드라이브 `0.서식(과제 관련)/연구보고서(유형별) …` 에
올리는 유형별 보고서 서식은 **개정될 때마다 그 폴더가 최신본**이지만, 번호·불릿에
outline 선언이 없는 **flat 스타일**이라 `hwp-agent write` 가 그대로는 못 먹는다.
개정될 때마다 사람이 손보는 대신, 이 루틴이 매일 새벽 한 번 돌며 **개정을 감지해
기계친화 초벌구이(normalize)** 를 하고 PR 로 올린다.

## 무엇을 하나

매일 **05:30**(launchd), 이 맥에서:

1. **WORKS Drive API**(`works ls`)로 대상 서식의 `modifiedTime` 을 조회 →
   개정/신규만 선별하고, 바뀐 것만 `works download` 로 받는다
2. (`.hwp` 면 `convert` →) **`normalize`** 로 `AI:HEADING_n`/`AI:BULLET_n` 사다리를
   선언한 사본을 굽는다 (한글 이름 보존, 컨테이너 보존)
3. `examples/house-forms/<이름>.normalized.hwpx` 로 커밋 — **격리된 git worktree**
   에서 단일 `auto/house-forms-refresh` 브랜치에 올리고 **PR 생성/갱신**
4. 본인 **Slack DM** 으로 요약 통지

변경이 없으면 조용히 종료한다.

### 설계 원칙

- **소스는 절대 수정 안 함.** 공유드라이브 원본은 읽기 전용(다운로드만), 사본에만 작업.
- **로컬 마운트 대신 WORKS API(`works` CLI).** launchd 백그라운드는 NAVER WORKS
  CloudStorage **FileProvider 마운트를 못 서비스**해 `open()` 이 무한 대기한다(실증됨).
  서비스계정 위임 API 로 받으면 헤드리스·이식 가능하고 이 행이 사라진다.
- **normalize 만 (초벌구이).** 견고한 스타일 기반 변환. 예시 본문을 걷어내는 완전
  golden template(슬롯·지침) 스켈레톤화는 표지/판권 문자열 매칭이 서식마다 달라
  개정 자동화에 취약하므로 **자동 범위에서 제외** — 필요할 때 사람이 수동 큐레이션
  (참고: [golden-template.md](golden-template.md)).
- **자동 커밋은 PR 로만, main 직접 push 안 함.** normalize 산출물은 한글에서
  **육안검증**(보안경고·레이아웃·F6 영문이름) 후 사람이 머지한다.
- **격리 worktree** 에서 git 작업 → 사용자의 라이브 체크아웃/작업 브랜치를 안 건드림.

## 스타일 명명 변경 대비 — override + 회귀 가드

전략기획실은 스타일을 **의미가 아니라 겉모양(WYSIWYG)으로** 매긴다 — "이렇게 보이면
돼" 하고 스타일 이름은 대충 붙인다(그림·표 번호도 꾸밈용까지 전부 번호, 불릿도 어떤
건 스타일·어떤 건 플랫 텍스트). 그래서 **이름 기반 자동추론(normalize)은 근본적으로
불안정**하고, 개정 때 명명이 바뀌면 소리 없이 깨질 수 있다. 두 겹으로 막는다:

1. **회귀 가드(자동).** 직전에 기계친화(`structured`/선언≥1)였던 서식이 개정 후
   `flat`/선언 감소로 떨어지면 **회귀**로 판정해 Slack DM·PR·로그에 🚨 로 크게 알린다
   (normalize 가 왜 못 잡았는지 경고까지 첨부). 상태파일에 서식별 classification·
   declarations 를 남겨 매 개정마다 직전과 비교한다. → 침묵하는 열화가 없다.
2. **명시적 매핑 override(사람).** 자동추론이 안 통하는 서식은 `overrides/<원본파일명>.json`
   에 **styleName → ROLE** 매핑을 두면 확정 적용된다(자동추론 우회). 렌더를 눈으로 보고
   장/절/소절·1불릿/2불릿을 판정해 만드는 게 안전 — 스타일 이름을 믿지 않는다. 예:

   ```json
   // overrides/정책이슈브리프.hwp.json
   { "styles": { "제목": "HEADING_1", "본문강조": "BULLET_1", "참고": "BULLET_2" } }
   ```

   구현은 `ops/normalize.apply_style_roles(path, mapping, out)` — 지정 스타일의
   engName 을 `AI:<ROLE>` 로 컨테이너 보존 치환. 명명이 바뀌어 매핑의 스타일 이름이
   사라지면 그 서식 bake 가 실패(→ 회귀 가드가 알림) → override 를 갱신하면 복구된다.

**흐름 요약:** 자동추론이 되는 서식(보고서 4종)은 그대로, 안 되는/깨진 서식은
override 로 확정. 회귀 가드가 "언제 override 가 필요한지"를 알려준다.

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
  — 소스별 마지막 초벌구이 시점의 WORKS `modifiedTime`. 지우면 전부 다시 굽는다.
- **소스 접근**: `works` CLI(서비스계정 위임). 공유드라이브 `WORKS_SD`, 폴더
  `WORKS_FOLDER_ID`, 대상 `TARGETS` 는 스크립트 상단. 인증은 works 설정
  (`~/.config/ji-works/.env` 등, `works doctor` 로 점검).
- **로그**: `~/.local/share/hwp-agent/refresh-forms.log` (+ launchd stdout
  `refresh-forms.launchd.log`).
- **Slack**: gw 의 봇토큰을 재사용한다(`SLACK_BOT_TOKEN`). 탐색 순서
  `~/.config/hwp-agent/refresh-forms.env` → `~/.config/ji-gw-ai/.env`. DM 대상은
  스크립트의 `NOTIFY_EMAIL`.

## 전제·한계

- **로그인 세션 전용**(LaunchAgent): git push(SSH 키)·`gh`(토큰)가 로그인 상태의
  키체인/에이전트에 기댄다. 로그아웃/로그인 화면에서는 push 단계가 막힐 수 있다.
  (소스는 works API 라 마운트·로그인과 무관.)
- **이식 여지**: 소스 취득이 works API 로 바뀌어 마운트 의존이 사라졌다. git/gh 자격만
  갖추면 이 루틴은 다른 머신·헤드리스에서도 돌 수 있다(현재는 이 맥 launchd 로 운영).
- **PR 이 안 머지된 채 소스가 또 개정되면** 같은 `auto/house-forms-refresh` 브랜치를
  force-push 로 갱신한다(PR 하나 유지). PR 을 머지 없이 닫으면 그 초벌구이는
  다음 소스 개정까지 재생성되지 않는다(상태파일이 이미 최신 해시라서). 필요하면
  상태파일에서 해당 항목을 지우고 재실행.
