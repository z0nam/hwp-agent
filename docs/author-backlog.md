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
