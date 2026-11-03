# Writing Markdown for `write` / `build`

Read this before generating Markdown for `hwp-agent write` or `build`. Footnotes·links, equations, figures and cover metadata have their own files (see the table in `SKILL.md`).

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
- `[^id]` + a `[^id]: 내용` definition line (pandoc/GFM footnotes) → a real
  Hangul **footnote** (각주) — see `references/write-notes-links.md`.
- `[표시](https://…)` and bare `https://…` URLs → Hangul **hyperlinks**
  (Ctrl+click opens) — see `references/write-notes-links.md`.
- `$…$` / `$$…$$` LaTeX math → Hangul **equation objects** (수식) — see `references/write-equations.md`.


## Cross-references — **optional**, ask the writer first

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

Figures get the same treatment with their own anchor (`references/write-figures.md`). The substitution is **static text** (matches Hangul's autonum
under the standard "표 {chapter}-{N}" convention); if a human reorders tables in
Hangul, the captions auto-renumber but inline refs stay frozen — re-run `write`
after reorder. An unresolved `{ref:id}` is left in place and surfaced as a
warning so it's easy to spot. Single-brace, so they never collide with the
template's double-brace tokens (`{{chapter_number}}` etc.). See JRI's standards
(`ji-report-standards/format/latest.md` 「표 상호참조 (선택)」) for the institute
convention.


## Template tokens (placed in the template, in Hangul, by a human)

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


## Tables (Markdown pipe tables → HWPX)

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

## Unspecified table reference — pause, don't guess

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
