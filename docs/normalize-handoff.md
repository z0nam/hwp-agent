# 진행상황 핸드오프 — 공식양식 hwp (normalize / golden template)

> 작업 재개용 스냅샷. 세션을 홈 루트(`/Users/namun`)에서 실수로 돌렸으나 편집은
> 모두 절대경로로 이 저장소에 들어갔고 **전부 커밋돼 있음** — 루트에 옮길 잔재 없음.
> cwd만 `~/dev/hwp-agent`로 두고 이어가면 된다. 스냅샷 시점 HEAD: `8d380a0`.

## 무엇을 하던 일인가

JI 공용드라이브 `0.서식(과제 관련)` 보고서 서식 4종(기반/센터/전략/**정책과제**)은
번호·불릿이 outline 선언 없는 flat 스타일이라 `hwp-agent write`가 그대로는 안 먹는다.
→ (1) flat 서식을 자동으로 기계친화화하는 `normalize`를 만들고, (2) 정책과제를
재사용 가능한 **golden template**(슬롯·지침 박힌 스켈레톤)으로 가공하는 작업.
근거: [`docs/author-backlog.md`](author-backlog.md) 항목 H, [`docs/golden-template.md`](golden-template.md).

## 완료 (커밋됨)

- **`hwp-agent normalize`** — 커밋 `26e1d8e`. flat 서식의 번호/불릿 사다리를 탐지해
  `AI:HEADING_n`/`AI:BULLET_n`을 **engName에** 자동 선언(한글 이름 보존), header.xml만
  바이트 치환 + 컨테이너 보존(`ops/container.py`). 파일: `ops/normalize.py`,
  `ops/styles.py`(`enumerator_class`/`bullet_glyph_rank`/`bullet_glyph_name`, H<n>별칭,
  선언 사다리 기반 classify), `ops/author.py`(`_heading_render_text`/`_bullet_render_text`
  — 비-OUTLINE 스타일에선 리터럴 번호·글리프 유지), `cli/main.py`, `mcp_server.py`.
  테스트 `tests/test_normalize.py`.
  - **원내 관행 반영:** 글리프 한 글자 이름의 일반 스타일(예 '-')=수동 불릿 대가리 →
    사다리에 포함(정책과제 BULLET_4), author가 글머리표를 리터럴로 재공급.
- **정책과제 golden template** — 커밋 `dba1b03`. `examples/정책과제-template.hwpx`
  (본문 걷어낸 스켈레톤 + `{{title}}`/`{{body}}` 등 슬롯 + `AI:INSTRUCTION` + 표참조 +
  고아 이미지 정리), 가공 스크립트 `scripts/build-golden-template.py`, 문서
  `docs/golden-template.md`. `hwp-agent check` → **structured, ✓ no issues**.
- **python-hwpx 2.11 정렬** — 커밋 `0509421`. deps·author·tests 적응.

## 테스트 상태

`uv run pytest tests/test_normalize.py tests/test_author.py` → **60 passed, 1 skipped**
(skip은 hwpx 2.11이 dirty 섹션 저장 시 layout cache를 벗기는 의도된 동작).

## 남은 일 (우선순위)

1. **한글 육안 검증 (게이팅 항목):** 정책과제 golden template과 normalize 산출물을
   한글 보안수준 '높음'에서 열어 ①보안경고 없음 ②레이아웃 무너짐 없음 ③스타일(F6)
   영문이름에 AI:HEADING_n/AI:BULLET_n. PoC 산출물: `/tmp/normalize-poc/norm-out.hwpx`,
   `written.hwpx` (재생성하려면 아래 명령).
2. **나머지 3종(기반·센터·전략) 전개:** 스타일 시스템 동일, 표지/판권 문자열 매칭만
   손보면 됨. 정책과제 한글 검증 통과 후.
3. **기능화(중요):** "원본 서식 → 기계친화 스켈레톤" 변환을 `hwp-agent` 명령으로 승격
   (normalize + 스켈레톤화 + 슬롯/지침/표참조 + 고아 이미지 정리). 지금은
   `scripts/build-golden-template.py`가 프로토타입. `check`를 진단→"기계친화 점수/
   수정 체크리스트"로 키우는 작업과 함께.
4. (범위 외, backlog H v1) 본문 수동 bullet 승격.

## 참고 명령

```bash
cd ~/dev/hwp-agent
# flat 서식 정규화 (사본에)
uv run hwp-agent normalize "/tmp/normalize-poc/정책과제_서식.hwpx" -o /tmp/normalize-poc/norm-out.hwpx
uv run hwp-agent classify /tmp/normalize-poc/norm-out.hwpx   # → structured
uv run hwp-agent check    /tmp/normalize-poc/norm-out.hwpx   # 사다리·이슈 확인
# golden template 사용 (본문 + 메타 분리)
uv run hwp-agent write content.md --template examples/정책과제-template.hwpx -o draft.hwpx
uv run hwp-agent form fill draft.hwpx --map meta.json -o final.hwpx
```

원본 서식(수정 금지, 사본 작업): `~/Library/CloudStorage/NAVERWORKSDrive-namun@ji.re.kr/Collaborative Drive/0.서식(과제 관련)/연구보고서(유형별) 서식 및 보도자료 서식/`
— 4종: `기반과제_서식.hwpx`·`센터과제_서식.hwpx`·`전략과제_서식.hwpx`·`정책과제_서식.hwpx` (모두 6월 10자; 서식은 개정되므로 #2 시 현재본으로 재빌드).
(이전 경로 `.../보고서 서식(유형별)/`은 폴더명 변경으로 무효.)
Claude 플랜 파일: `~/.claude/plans/docs-author-backlog-md-h-flat-template-n-peppy-meteor.md`.
