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
   style system). For a `weak`/`flat` *report template* whose numbering exists
   only as plain styles (로마자/'1.'/'1)'), run
   `hwp-agent normalize work.hwpx -o work.normalized.hwpx` — it declares
   `AI:HEADING_n`/`AI:BULLET_n` automatically and the copy classifies as
   structured (have a human verify it opens cleanly in Hangul). On a normalized
   template the heading numbers are **literal text**, so type them in the
   Markdown yourself (`# Ⅰ. 서론`, `## 1. 추진 배경`) — nothing auto-numbers.
   For a `flat` *form*, fall back to form-fill or ask the human.

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
     (or `--map values.json`). Slot keys may be a label (`성명`), a label path
     (`성명 > right` / `> below`), a stable address (`cell:<table>:<row>:<col>`,
     also emitted as `cell_path` by `analyze`), a `checkbox:<label>` set `on`/`off`
     (□↔■), a `tab:<anchor>` inline field, or a `{{placeholder}}`. Fills overwrite
     (true SET), so re-running is safe.
   - **Profile auto-fill** (the same person's standing data across many forms) —
     `hwp-agent form fill work.hwpx --profile ~/.config/hwp-agent/profile.json
     --date today -o out.hwpx`. Maps 성명/생년월일/주소/연락처/학력/경력/계좌 from a
     saved JSON (copy `examples/profile.example.json`) onto matching slots; reports
     what was filled vs left blank. Repeated 학력/경력 rows map under their header.
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

### Cross-references — **optional**, ask the writer first

When the same table is referenced from prose, the writer *can* label it once and
cross-refer instead of hand-typing the number. This is **opt-in**: many writers
don't know the convention, and forcing it confuses more than it helps. So:

- **Before generating Markdown**, ask the writer once: "표 상호참조 토큰
  (`{ref:id}`)을 사용할까요? 모르시면 '사용 안 함'으로 답해 주세요." Use it only
  on a clear yes; on no/unsure, refer to tables by natural Korean context
  ("아래 표와 같이 〜", "위 등급표에 의하면 〜").
- **Declare** on a table's caption: `자료 신뢰도 등급 {label:tbl_grade}` → the
  `{label:tbl_grade}` is stripped from the rendered caption and registered.
- **Reference** anywhere (prose, cells, captions, notes): `… 등급은 {ref:tbl_grade}
  참고 …` → replaced at fill time with the table's autonum text, e.g.
  `표 부록-3` (chapter from `--chapter` + the table's 1-based document order).

Tables only (v1). The substitution is **static text** (matches Hangul's autonum
under the standard "표 {chapter}-{N}" convention); if a human reorders tables in
Hangul, the captions auto-renumber but inline refs stay frozen — re-run `write`
after reorder. An unresolved `{ref:id}` is left in place and surfaced as a
warning so it's easy to spot. Single-brace, so they never collide with the
template's double-brace tokens (`{{chapter_number}}` etc.). See JRI's standards
(`ji-report-standards/format/latest.md` 「표 상호참조 (선택)」) for the institute
convention.

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
- The **caption title** = the line directly above the table (one blank line
  between is also fine).
- **Do not type the `<표 …>` / `[그림 …]` framing** in the caption — the template's
  autonum supplies it. Typing it produces a doubled `<표 부록-1> <표 부록-1>` prefix.
  Just write the title text (e.g. `참여자 효과 자료 신뢰도 등급`); the tool also
  strips a leading framing defensively if it slips in.
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
| `hwp-agent normalize FILE.hwpx [-o OUT] [--dry-run] [--json]` | declare `AI:HEADING_n`/`AI:BULLET_n` on a flat template's number/bullet styles (never in-place; default `<input>.normalized.hwpx`) |
| `hwp-agent instructions FILE.hwpx [--json]` | AI:INSTRUCTION directions + `{{slots}}` |
| `hwp-agent extract FILE.hwpx [--body-only] [-o OUT.md]` | extract HWPX as body-focused Markdown; merged cells flattened (Excel-style) |
| `hwp-agent form analyze FILE.hwpx [--json]` | list fillable slots |
| `hwp-agent form fill FILE.hwpx --set K=V [-o OUT]` | fill slots by name |
| `hwp-agent write C.md --template FILE.hwpx [--chapter N] [--table-template CAPTION] [-o OUT]` | write Markdown into a template (`author` = alias) |
| `hwp-agent image list FILE.hwpx [--json]` | list figure image slots (ref, format, px size, caption) |
| `hwp-agent image replace FILE.hwpx IMG --ref image7 [--fit aspect\|none] [-o OUT]` | swap one figure image in place (`--caption "[그림 …]"` also targets it) |
| `hwp-agent meta FILE.hwpx [--set K=V]` | read/set document metadata |

`-o/--output` writes to a new file; omit it to edit in place. Point at a jar
elsewhere with `--jar` or `$HWP2HWPX_JAR`.

## Extracting HWPX → Markdown

`hwp-agent extract FILE.hwpx -o draft.md` reads the document back as
body-focused Markdown — the inverse of `write`. Headings, bullets, ordered
lists, and body paragraphs roundtrip to their Markdown equivalents.

**Tables are flattened (Excel-style unmerge).** A `<hp:cellSpan>`-merged cell
has its value duplicated into every position it covered, so the table fits
Markdown's strictly rectangular `| … |` shape. Data is preserved exactly;
merge intent is lost — the doubled-value pattern signals where merges existed.
Multi-paragraph cells use `<br>` to keep paragraph breaks readable. v1 scope:
no inline emphasis detection (runs come out as plain text), no image bytes
(figures are emitted as their caption text only).

Primary use: the **"messy HWP → MD → re-write into a clean template"** loop.
Read a non-conforming HWPX (or one drafted off-template), edit the Markdown to
fit the JRI tone / format / cross-ref conventions, then `write` it back onto a
proper template. `--body-only` skips everything before the first level-1 heading
(cover, TOC) — handy for AI digestion of just the substantive body.

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

2. **Strip stale `<hp:linesegarray>` from every paragraph you touch — and from
   every paragraph you generate.** Each paragraph caches its per-line layout
   (one `<hp:lineseg>` per visual line) there, and Hangul *trusts the cache* on
   open instead of always recomputing. Two failure modes, both seen in real
   files:
   - **No wrapping.** Inject longer text into a cell that was empty (a 1-line
     cache) and it renders on a single overflowing line — `lineWrap="BREAK"`
     alone is not enough.
   - **Overlapping stacked lines.** Generate *new* paragraphs (or duplicate a
     template paragraph) with a single-line `linesegarray`, give them text that
     wraps to 2+ lines, and every wrapped line is drawn at the **same vertical
     position** — the text renders as an unreadable mush. Short single-line
     paragraphs look fine, so the bug hides until a long bullet wraps.

   You can't pre-compute the correct lineseg (you don't know the wrap points),
   and a plain text-only `.replace()` on an existing paragraph leaves its old
   cache in place too. So don't try to patch it per-paragraph — the robust fix
   is to **strip `linesegarray` document-wide** before repackaging
   (`re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", xml, flags=re.S)`
   plus removing any self-closed `<hp:linesegarray/>`). Hangul then recomputes
   the entire layout on open. Verify by rendering the result to PDF with
   LibreOffice (it ignores the cache and lays out fresh) — clean wrapping there
   means the markup is sound; final proof is opening in Hangul.

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
