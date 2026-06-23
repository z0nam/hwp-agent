# 출력 덮어쓰기 가드 (overwrite-guard) — 구현됨

## 배경 / 문제
`write` / `form fill` / `image replace` 가 출력 파일을 **무조건** 새로 썼다. 그 파일이
*이전 hwp-agent 출력 이후 한글에서 사용자가 수정한 것*인지 확인하지 않았다
(`cli/main.py`: *"output paths are never checked"*).

실제 사고: hwp-agent가 만든 HWPX를 한글에서 열어 그림을 삽입·저장 → 이후 같은 파일명으로
재생성 → 그림 작업이 통째로 덮어써짐(복구 불가).

## 정책 (2026-06-23 확정·구현)
출력 직전에 판정:
- 대상 **없음** → 그냥 쓴다.
- 대상이 **내가 쓴 그대로**(내장 지문 == 현재 내용 해시) → 덮어쓴다.
- 대상이 **외부 수정됨**(지문 불일치) 또는 **내 지문 없음**(외부 파일) → **버전 생성**
  (`out_v2.hwpx`, `out_v3.hwpx` …)으로 저장, 기존 파일은 **그대로 보존.** 절대 안 덮어씀.

→ `--force`/중단 없이도 **작업이 절대 안 날아가고, 막히지도 않음.** 연속 hwp-agent 실행은
"내가 쓴 그대로"라 그냥 덮어쓰므로 마찰 없음.

## 메커니즘 — HWPX 내장 지문 (사이드카 없음)
프로비넌스 지문을 **.hwpx 안 숨김 OPC 파트** `META-INF/hwpagent.sha256` 에 저장:
`{ sha256(나머지 모든 파트), tool, v, written_at }`. self-hash 회피 위해 *지문 파트를 제외한*
나머지 파트의 해시.

- **폴더 클러터 0**, 파일과 함께 이동(메일·드라이브·복사).
- **한글로 열어 저장하면** 패키지가 통째로 재작성 → 지문이 사라지거나 해시 불일치 →
  **자동으로 drift 감지**(= 의도된 동작).

**호환성 실측 (2026-06-23)**: python-hwpx ✅ · rhwp(export-pdf) ✅ · **한글(namun-ji) 열기+변환 ✅**.
(숨김 파트는 열 때 무시됨.)

## API (`ops/guard.py`)
- `plan_output(path, *, force=False) -> GuardResult(target, versioned, reason)` — 쓰기 *전* 호출.
- `stamp_fingerprint(path)` — 쓰기 *후* 호출(지문 내장, mimetype-first 재패킹).
- `is_ours_untouched(path)` · `read_stored_fingerprint(path)` · `current_content_hash(path)` ·
  `next_versioned_path(path)`.
- 단일 헬퍼라 CLI·MCP·서버가 공유 가능.

## 적용 범위 (현재)
- CLI: **`write`**, **`form fill`**(+`--profile`), **`image replace`**. 각 명령이
  `_guard_output()`로 target 결정 → ops가 거기 쓰기 → `_guard_finalize()`로 지문 stamp +
  버전 시 안내(`⚠ 덮어쓰기 가드: … → 새 버전으로 저장`).
- 미적용(후속): `meta --set`, `normalize`, `convert`, MCP/서버 경로. 같은 헬퍼로 확장만 하면 됨.

## 한계 / 비고
- 외부 파일(지문 없음)에 in-place(`-o` 없이)로 쓰면 첫 실행부터 버전 생성됨 — 안전하나
  "제자리편집" 기대와 다를 수 있음(원본 보존이 우선이라 의도된 트레이드오프).
- 본질은 버전관리가 아니라 **drift 감지**: 마지막에 쓴 바이트와 현재 디스크 바이트가 다르면
  = 누군가 손댐 = 말없이 덮지 말 것.
