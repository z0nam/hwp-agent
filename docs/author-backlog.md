# `ops.author` hardening backlog

Concrete `hwp-agent author` defects and improvements found while authoring a real
appendix (서귀포 보고서, 2026-05-24) — see `docs/section-split.md` for the case.
Each item has the observed symptom, the diagnosis, and the fix direction. None
implemented yet; ordered roughly by impact.

## A — `{{table_template}}` token is consumed on author *(format regression trap)*

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

## C — author headings lack `<hp:linesegarray>` → Hangul demotes 2nd+ headings

- **Symptom:** only the **first** generated outline heading stays a heading;
  Hangul **demotes the rest to body**, even though style / paraPr / charPr are
  byte-identical to a real heading.
- **Diagnosis:** author-built heading paragraphs are missing the
  `<hp:linesegarray>` (outline flags `2490368`) that genuine headings carry.
- **Fix:** when emitting a heading paragraph, **include a `linesegarray` matching
  the document's real headings** — in practice, clone a known-good heading
  paragraph of that style and replace only its text, rather than building from
  the style id alone.

## E — table width not aligned to the text column

- **Symptom:** generated tables have arbitrary absolute widths (seen
  21600–43200). A 6-column table **overflowed the text width (36850)**; small
  tables fell short of it.
- **Diagnosis:** cell widths are copied from the reference zones without scaling
  to the target document's text column.
- **Fix:** scale each table `<hp:sz width>` and every `<hp:cellSz width>` to the
  **text width (= page width − L/R margins)** proportionally — expand small
  tables, clamp large ones — and absorb rounding drift into the last cell per row.

## F — inline bold / sub-headings map to oversized direct formatting

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

## B / D — section-split & custom outline numbering *(tracked as M8)*

- **B:** an optional `--appendix` / `--new-section` helper. But the **safest path
  is the Hangul-made empty section + token**, not XML synthesis — see
  `docs/section-split.md`.
- **D:** support a **user-specified outline-number format** (e.g. `A.1.1`: L1
  `LATIN_CAPITAL`, then `^1.^2`, `^1.^2.^3`), put the appendix top level at outline
  **level 0**, and reconcile the **start number** (remove the ghost "A." empty
  outline paragraph so the body doesn't start at "B.").
