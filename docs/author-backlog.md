# `ops.author` hardening backlog

Concrete `hwp-agent write` (the command formerly named `author`) defects and
improvements found while authoring a real
appendix (서귀포 보고서, 2026-05-24) — see `docs/section-split.md` for the case.
Each item has the observed symptom, the diagnosis, and the fix direction.

**Status (2026-05-24):** A, C, E, and the size-preserving half of F are
**implemented** (verified structurally + `U+FFFD`-free; **C and F still want a
Hangul render check**). The named-style sub-heading mapping (F) stays deferred,
and B/D (section-split / custom outline numbering) are M8.

## A — `{{table_template}}` token is consumed on author *(format regression trap)* — ✅ done

- **Symptom:** a once-authored file reused as a base has **lost the token**, so
  the next `author` falls back to a generic default table style (pale blue/pink
  header + 1pt data) instead of the house format.
- **Diagnosis:** `_strip_table_token` removes the token on fill (by design). With
  the token present, tables correctly clone the reference table's `borderFill`
  (e.g. header `#2E5A88`) and cell style (`JRI_표내용`, 9.5pt). Every table-format
  problem traced back to a missing token.
- **Fix:** **warn when no `{{table…}}` token is found** on a doc that has tables
  (don't silently use the generic default); and/or accept the reference as an
  option, e.g. `--table-template "<caption pattern>"`, so it doesn't depend on a
  token surviving a prior run.
- **Done:** `AuthorResult.warnings` carries the warning (CLI prints it to stderr)
  when the Markdown has tables but no token/pattern matched; `--table-template
  CAPTION` (a caption substring) selects the reference when the token was consumed.

## C — author headings lack `<hp:linesegarray>` → Hangul demotes 2nd+ headings — ✅ done (needs Hangul check)

- **Symptom:** only the **first** generated outline heading stays a heading;
  Hangul **demotes the rest to body**, even though style / paraPr / charPr are
  byte-identical to a real heading.
- **Diagnosis:** author-built heading paragraphs are missing the
  `<hp:linesegarray>` (outline flags `2490368`) that genuine headings carry.
- **Fix:** when emitting a heading paragraph, **include a `linesegarray` matching
  the document's real headings** — in practice, clone a known-good heading
  paragraph of that style and replace only its text, rather than building from
  the style id alone.
- **Done:** `_lineseg_index` maps each styleIDRef to a deep-copied `linesegarray`
  from a real same-style paragraph; an authored heading gets that clone appended.
  **Still wants Hangul confirmation that the demotion is actually gone.**

## E — table width not aligned to the text column — ✅ done

- **Symptom:** generated tables have arbitrary absolute widths (seen
  21600–43200). A 6-column table **overflowed the text width (36850)**; small
  tables fell short of it.
- **Diagnosis:** cell widths are copied from the reference zones without scaling
  to the target document's text column.
- **Fix:** scale each table `<hp:sz width>` and every `<hp:cellSz width>` to the
  **text width (= page width − L/R margins)** proportionally — expand small
  tables, clamp large ones — and absorb rounding drift into the last cell per row.
- **Done:** `_text_width` reads the section's `pagePr` width minus L/R margins;
  `_fit_table_width` scales every row to it (drift → last cell) before styling.

## F — inline bold / sub-headings map to oversized direct formatting — ◑ partial (size-preserving done; sub-heading mapping deferred)

- **Symptom:** a Markdown bold sub-heading (`**입력 데이터 구성**`) was mapped to a
  **20pt charPr** (chapter-title size). Related: appendix table data cells were
  built with `styleIDRef="0"` (바탕글) + **direct** char formatting that reused the
  document's **1pt charPr** → cells effectively invisible at 1pt.
- **Diagnosis:** the known `ensure_run_style` size bug (picks a wrong-size
  existing charPr) plus reliance on **direct character formatting** instead of a
  named style. This also violates the font-hierarchy principle
  (`docs/template-convention.md`).
- **Fix / principle:** **prefer named styles over direct formatting.** Cells →
  the document's table-content style (e.g. `JRI_표내용`, id 14). Inline
  emphasis / sub-headings → an appropriate named sub-heading style (9–11pt bold),
  never a chapter-title charPr and never 1pt. Direct charPr edits are a last
  resort. (Item 6 of the report folds in here: with the `{{table_template}}` token
  present, author already styles cells correctly — see **A**.)
- **Done (size half):** `_emphasis_char` builds a bold/italic charPr that
  **preserves the base size** (matches the base charPr's height, cloning from it
  when needed), so inline `**bold**` no longer grabs a 20pt charPr.
- **Deferred:** mapping a whole-line bold paragraph to a named sub-heading style
  (a heuristic) stays a future rule, as flagged in the report.

## B / D — section-split & custom outline numbering *(tracked as M8)*

- **B:** an optional `--appendix` / `--new-section` helper. But the **safest path
  is the Hangul-made empty section + token**, not XML synthesis — see
  `docs/section-split.md`.
- **D:** support a **user-specified outline-number format** (e.g. `A.1.1`: L1
  `LATIN_CAPITAL`, then `^1.^2`, `^1.^2.^3`), put the appendix top level at outline
  **level 0**, and reconcile the **start number** (remove the ghost "A." empty
  outline paragraph so the body doesn't start at "B.").

## G — bullet nesting isn't the HWP outline level *(role map + check)* — ◑ check done; role map honors AI:BULLET_n

- **Symptom:** the role map collapses sibling bullets — `■` (10.5pt, used 265×)
  and `-` (10.0pt, used 394×) both sit at HWP outline level 0, so only one becomes
  `BULLET_1` and the other is dropped; `check` then flags `■ > -` as a false
  font-hierarchy violation.
- **Diagnosis:** in HWP, **bullet nesting is encoded by the bullet style (glyph),
  not the outline level** — `■` is the parent, `-` nests under it. The
  "outline level = nesting" rule (fine for headings) is wrong for bullets.
- **Fix:** order the bullet ladder by an **explicit declaration**, not the outline
  level. Honor the existing `AI:BULLET_n` naming override first; optionally fall
  back to a stable convention (e.g. font size descending, or a glyph order
  `■ > ● > - > ·`). Then `check` must judge bullet hierarchy against that order
  (so `■` 10.5 > `-` 10.0 reads as correct), and stop calling true sub-level
  bullets "un-mapped siblings".
- **Done:** `check` no longer gap/size-checks the BULLET ladder (those derive
  from the unreliable outline level); it surfaces the un-targetable bullet styles
  with explicit guidance to declare `AI:BULLET_n`. `role_map` already honors that
  `AI:BULLET_n` naming override outright, so a declared ladder works today.
- **Deferred:** the convention fallback (glyph/size order when *no* `AI:BULLET_n`
  is declared) — until then, an undeclared multi-bullet template needs the naming.

## H — flat-template normalizer (실서식 → hwp-agent 친화 변환 모듈) — ☐ proposed

- **Trigger (2026-06-12 survey):** JI 웍스드라이브 `Collaborative Drive/0.서식(과제 관련)`
  — 원내 과제 서식의 공식 SSOT, 인덱스는 `ji-regulations/forms/works-drive-index.yaml` —
  의 hwp/hwpx 16종을 전수 분류한 결과 **15 flat / 1 weak / 0 structured**.
  (9개 .hwp 전부 jar 변환은 무손실 성공 — 문제는 변환이 아니라 서식 자체의 스타일 체계.)
  - **신청서류** (연조위 5종 + 수행계획서 등): flat이지만 `form analyze`가 슬롯
    6~90개를 잡음 → **form-fill 경로는 현재도 사용 가능**. 변환 불요.
  - **보고서 서식(유형별) 4종** (기반/센터/전략/정책과제_서식.hwpx): 4종 모두 동일 패턴 —
    styles=19 중 roled=3 (BODY, BULLET_1 ￭, BULLET_3 -), **HEADING ladder (none)**.
    번호 체계가 일반 스타일로만 존재(로마자 20pt, '1.' 13pt, '1)' 11pt — outline 미선언),
    un-mapped bullet ⦁(4×) + 구조 스타일 7종(표제목·자료·단위 등).
    작성자마다 bullet 수동 입력·스페이스 들여쓰기 제각각이라 실문서는 더 심함.
- **Implication:** 원내 공식 보고서 서식에서 `write`(author) 경로가 그대로는 불가.
- **Fix idea — `hwp-agent normalize IN.hwpx -o OUT.hwpx`:**
  1. ladder 후보 스타일 탐지 (번호 글리프·폰트 크기 내림차순 휴리스틱 — G의 fallback과 공유),
  2. `AI:H<n>` / `AI:BULLET_n` 선언을 자동 부여 (지금은 한글에서 수동으로만 가능한 작업의 자동화
     — `check`가 처방하는 fix 그대로),
  3. (선택) 본문의 수동 bullet("- ", "•", 스페이스 들여쓰기)을 선언된 스타일로 승격,
  4. 변경 내역 리포트 출력 → 사람이 한글에서 검수.
  컨테이너 보존 규칙(원본 ZipInfo 유지, linesegarray 제거)은 기존 hand-edit 노하우 재사용.
- **Interim workaround:** 보고서 서식 4종 사본에 한글에서 `AI:H<n>`/`AI:BULLET_n`을
  수동 선언한 "authoring 판"을 만들어 쓰고, 정본 폴더와 별도 관리 (ji-regulations 쪽 메모 참조).
