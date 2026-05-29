---
name: hwp-agent
description: >-
  Author and fill Korean HWP/HWPX documents in their native format (no lossy
  DOCX round-trip), using the `hwp-agent` CLI. Use this when asked to convert
  an .hwp to .hwpx, fill an HWP/HWPX form or template, write report content
  into an HWP template from Markdown, inspect a template's styles / form slots /
  embedded AI directions, or classify how structured an HWP document is.
---

# HWP/HWPX authoring with `hwp-agent`

`hwp-agent` edits HWP/HWPX — the document standard across Korean public and
research institutions — **directly in the native format**, so Korean-specific
formatting (cover layouts, 표/table styling, 머리말/꼬리말, numbering, fonts) is
preserved. Going through DOCX silently mangles these; do not do that.

The original `.hwp` is the source of truth and is **never modified**. The
`.hwpx` is a regenerable cache artifact — safe to delete and rebuild.

## Prerequisites

- `hwp-agent` installed (the CLI). Verify with `hwp-agent --version`.
- To convert `.hwp` → `.hwpx`, the converter jar must be built
  (`./scripts/bootstrap.sh`, needs JDK 17+ & Maven). Editing existing `.hwpx`
  needs no jar.
- If `hwp-agent` is not on PATH, run it from the repo via the project venv:
  `.venv/bin/hwp-agent …`.

## The authoring loop

Given a template/form and a content intent, work in this order. **Always
inspect before you fill** — the template's own styles and directions drive the
output.

1. **Convert if needed.** If you were handed a `.hwp`, make a `.hwpx` first:
   `hwp-agent convert source.hwp work.hwpx`. Leave the `.hwp` untouched.

2. **Classify** the document so you pick the right strategy:
   `hwp-agent classify work.hwpx` → `structured` | `weak` | `flat`.
   This skill's `write` flow targets **structured** templates (a real outline
   style system). For `weak`/`flat`, fall back to form-fill or ask the human.

3. **Read the style roles** the template exposes (role → style id):
   `hwp-agent styles work.hwpx` (add `--json` for machine use). Roles include
   `HEADING_1..n`, `BULLET_n`, `ORDERED_n`, `BODY`. You don't set styles
   yourself — you write Markdown and the tool projects it onto these. To audit a
   template's completeness (missing ladder levels, font-hierarchy violations,
   bullet/structural styles the role map can't reach), run
   `hwp-agent check work.hwpx` — the fix is usually to declare `AI:BULLET_n` /
   `AI:H<n>` on the unreachable styles in Hangul.
   For a deeper read, `hwp-agent check work.hwpx` flags style-system problems
   (ladder gaps, font-hierarchy violations, bullet styles the role map can't
   target, un-mapped structural styles) — use it when authoring quality matters
   or the template's roles look incomplete.

4. **Read embedded directions** the template carries:
   `hwp-agent instructions work.hwpx` (`--json`). This surfaces
   `AI:INSTRUCTION`-styled paragraphs (authoring guidance written into the
   template by a human) and any `{{slots}}`. Obey those directions.

5. **Choose the path:**
   - **Form/slot fill** (fixed fields: dates, names, table cells with labels) —
     `hwp-agent form analyze work.hwpx --json` to list slots, then
     `hwp-agent form fill work.hwpx --set "신청일=2026-05-24" -o out.hwpx`
     (or `--map values.json`).
   - **Free authoring** (writing report body content) — write Markdown, then
     `hwp-agent write content.md --template work.hwpx -o out.hwpx`
     (positional = the Markdown you're writing; `--template` = the .hwpx to fill).

6. **Verify.** Open the output and confirm it's intact: reopen with the tool
   (`hwp-agent meta out.hwpx` round-trips it) and, when possible, have the human
   open it in Hangul. Generated text must be free of `U+FFFD` (�) replacement
   characters.

## Writing Markdown for `write`

The AI writes Markdown; the tool maps it onto the template's styles:

- `#`/`##`/`###` → the template's Heading 1/2/3 (outline numbering comes for
  free — never type "1." / "1.1" yourself). Any manual leading number you do
  type (`## 1.1 배경`, `## 부록 A: …`, `### A-1 …`) is **stripped** so it doesn't
  double up with the template's auto-number; the title text is kept.
- `- ` → bullet styles; `1. ` → ordered styles; plain lines → Body.
- `**bold**` / `*italic*` inline emphasis becomes runs.
- `| a | b |` + `|---|` + rows → an HWPX **table** (see below).
- `---` (or `***` / `___`) on its own line → a full-width **horizontal line**
  (가로선), as its own paragraph.

### Template tokens (placed in the template, in Hangul, by a human)

- `{{body}}` / `{{appendix}}` — each on its own paragraph, an **insertion
  marker**: `{{body}}` marks **where the main body begins** (the start of 본문 /
  chapter 1, after 표지·목차); `{{appendix}}` marks **where an appendix begins**.
  Authored content is inserted starting at that point and the marker paragraph is
  **consumed** (removed) on fill. They define a start boundary, not a generic
  "fill here" hole. Without one, content is appended to the last section. (If a
  template has both, the first in document order is used.)
- `{{table_template}}` (any `{{table…}}` form) in a **table's caption** marks
  that table as the **format reference** — generated tables copy its borders,
  cell styles, header look, and geometry. The token is **consumed on each
  `write` run**: if you re-write a file, pass `--table-template "<caption
  text>"` to keep copying the right table (the tool warns when tables are
  written with no token or pattern matched).
  **If the Markdown contains tables but neither a `{{table_template}}` token nor
  `--table-template` resolves a reference, STOP — do not write.** The tool's
  silent fallback ("the first table in the document") is dangerous: the first
  table is usually a complex main-body table, and its per-cell borders/shading
  get cycled onto your simple tables and corrupt them. Pause and ask the human
  to (a) tag the intended reference table's caption with `{{table_template}}`
  (or name it via `--table-template`), or (b) explicitly approve proceeding with
  **plain default tables** (no format copying). Only after their answer do you
  run `write`. See "Unspecified table reference" below.
- `{{chapter_number}}` in that caption is replaced with the chapter you supply
  via `--chapter`. `{{chapter_number=3}}` **forces** a value inline (wins over
  `--chapter`) — a worst-case override.

### Tables (Markdown pipe tables → HWPX)

- Markdown (GFM) tables are **rectangular only** — no merged cells. For merged
  tables, the human edits them in Hangul, or hands over a Sheet/HWPX draft to
  fill via the form path.
- The **caption title** = the line directly above the table.
- A **note/source row** = lines directly below starting with `주)` / `출처)` /
  `자료:`. With no such line the note row stays empty.
- Pass the chapter label explicitly: `--chapter 7` (or `--chapter 가`,
  `--chapter Ⅲ` — any label renders verbatim). **Auto-detection is
  unreliable** on real institutional documents (chapter titles live inside
  tables/boxes, outline use is inconsistent, numbering restarts per section), so
  always pass `--chapter`, or force it inline with `{{chapter_number=값}}`.

### Unspecified table reference — pause, don't guess

Before authoring any document that has Markdown tables, decide the format
reference **explicitly**:

1. Is there a `{{table_template}}` token in some table's caption, or are you
   passing `--table-template "<caption>"`? If yes, proceed.
2. If **no** reference resolves, do **not** rely on the tool's first-table
   fallback. **Pause and ask the human**, e.g.:
   > 채울 Markdown에 표가 있는데 `{{table_template}}` 참조 표가 지정되지 않았습니다.
   > (a) 기준으로 쓸 표의 캡션에 `{{table_template}}`를 달아 주시거나, (b) 서식
   > 복사 없이 **기본 표(테두리만 있는 단순 표)**로 진행할지 알려주세요.
3. Act on their answer:
   - **(a) reference given** → re-run with `--table-template "<caption>"` (or the
     re-tagged template).
   - **(b) proceed plain** → author with the **plain default table** policy: no
     copying of any existing table's per-cell styling. Hand `hwp-agent` this
     session instruction verbatim so the intent is unambiguous:
     > 표 서식 참조가 미지정 상태로 승인됨. 문서의 첫 표(또는 임의 표)에서
     > 셀 테두리·음영·열폭을 복사하지 말 것. 각 Markdown 표는 헤더행만 구분된
     > 단순 기본 표(균일 테두리, 음영 없음, 균등 열폭)로 생성할 것.

     (If the installed `hwp-agent` has no plain-default switch, this is also the
     message to file against the tool: the no-reference fallback should emit a
     plain table, not silently copy the first table.)

## Command reference

| command | purpose |
|---|---|
| `hwp-agent convert IN.hwp OUT.hwpx` | HWP → HWPX (needs the jar) |
| `hwp-agent classify FILE.hwpx` | structured / weak / flat |
| `hwp-agent styles FILE.hwpx [--json]` | machine style roles (role → style id) |
| `hwp-agent check FILE.hwpx [--json]` | check the style system: ladder gaps, font-hierarchy violations, un-mapped bullet/structural styles (`doctor` = alias) |
| `hwp-agent instructions FILE.hwpx [--json]` | AI:INSTRUCTION directions + `{{slots}}` |
| `hwp-agent form analyze FILE.hwpx [--json]` | list fillable slots |
| `hwp-agent form fill FILE.hwpx --set K=V [-o OUT]` | fill slots by name |
| `hwp-agent write C.md --template FILE.hwpx [--chapter N] [--table-template CAPTION] [-o OUT]` | write Markdown into a template (`author` = alias) |
| `hwp-agent image list FILE.hwpx [--json]` | list figure image slots (ref, format, px size, caption) |
| `hwp-agent image replace FILE.hwpx IMG --ref image7 [--fit aspect\|none] [-o OUT]` | swap one figure image in place (`--caption "[그림 …]"` also targets it) |
| `hwp-agent meta FILE.hwpx [--set K=V]` | read/set document metadata |

`-o/--output` writes to a new file; omit it to edit in place. Point at a jar
elsewhere with `--jar` or `$HWP2HWPX_JAR`.

## Pitfalls

- **Never go through DOCX.** It loses Korean formatting silently.
- **Don't synthesize numbering** (chapter/section/list/table numbers). Reuse the
  template's styles and let HWPX auto-numbering produce them; only the chapter
  *label* for captions is supplied by you (`--chapter`).
- **Don't hand-edit `.hwpx` XML** unless you know it; prefer the CLI.
- **Inspect first.** Run `classify` → `styles` → `instructions` before authoring.
- This flow is solid for **structured** templates; be cautious on `weak`/`flat`.

## Replacing figure images

`hwp-agent image list FILE.hwpx` enumerates every figure slot — its `ref`
(`binaryItemIDRef`, e.g. `image7`), the slot's stored format, the original pixel
size, and the caption text from the pic's own paragraph (a pic ↔ caption is 1:1;
the list-of-figures section has captions with no pic and is skipped). Replace one
with `hwp-agent image replace FILE.hwpx new.png --ref image7` (or `--caption
"[그림 III-2] …"`). Two rules the tool enforces for you, both verified against a
real report:

- **Format must match the slot.** Hangul keys off the file *extension*, not the
  (often `image/unknown`) media-type, so a `.png` slot needs PNG bytes. A
  mismatch is **refused** (`format_mismatch`) and nothing is written — re-encode
  the image to the slot's format first, or pick a file that already matches.
- **`--fit aspect` (default) keeps the box width and recomputes the height** so
  the new image isn't stretched into the old one's aspect ratio; `--fit none`
  leaves the display box untouched. The swap is byte-only and container-preserving
  (see below), so the edited file still opens at Hangul's 높음 security level.

## When you must hand-edit HWPX (flat forms `form fill` can't target)

For a `flat` form whose slots repeat (e.g. an evaluation sheet with one section
per item and identical `점수`/`검토의견`/`총평` slot names in every section),
`form fill --set name=value` can't disambiguate which item it targets. Editing
the section XML directly is then the pragmatic fallback — but two things will
silently break a file that otherwise round-trips fine through `meta`:

1. **Preserve the ZIP container — never rewrite it from scratch.** Hangul treats
   an HWPX whose ZIP differs from a native one (compression method, entry order,
   `mimetype` not first/STORED) as externally tampered and refuses to open it at
   the normal security level (보안 경고). The unedited `convert` output opens at
   "높음" with no warning; a full-rewrite copy with *identical text* triggers the
   warning — the difference is the container, not the content. So read
   `infolist()`, mutate only the bytes of the parts you change, and re-emit with
   the **original `ZipInfo` per entry, in original order** (`writestr(info,
   data)`), not `ZipFile('w', ZIP_DEFLATED)` + fresh `writestr(name, …)`.

2. **Strip stale `<hp:linesegarray>` from every paragraph you edit.** Each
   paragraph caches its line layout there. Inject longer text into a cell that
   was empty (a 1-line cache) and Hangul renders it on a single line with no
   wrapping — `lineWrap="BREAK"` alone is not enough. Remove
   `<hp:linesegarray>…</hp:linesegarray>` from edited paragraphs so Hangul
   recomputes wrapping on open.

Match cells by **label-relative position** (the score/opinion cells follow their
label cell), not absolute index — non-budget items insert an extra 참고사항
table that shifts indices. Match each section to its item by the task code in
the 과제명 cell, so section order is irrelevant. Verify with `meta` (round-trip)
**and** a `U+FFFD` scan, but final proof is opening in Hangul at 높음.

## Deeper reference

- `references/template-convention.md` — full machine-friendly template convention.
- `references/tables.md` — the tiered table strategy and what's copied from the
  reference table.
- `references/images.md` — figure-image anatomy and the byte-swap / container /
  format / aspect rules behind `image replace`.
