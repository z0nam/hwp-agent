# Tables — channel strategy

Markdown can't express merged cells (셀병합) — GFM tables are strictly rectangular.
HWPX and python-hwpx, however, fully support merged tables (build with
`add_table` + `merge_cells`/`set_span`; read/fill existing ones — including merges —
with `find_cell_by_label`/`fill_by_path`). So the limitation is the *input format*,
not the toolkit. We therefore route tables by complexity, keeping **Markdown as the
machine-authoring channel** and letting **humans edit tables in Hangul**.

## Three channels

1. **Simple (rectangular) tables → Markdown pipe tables.** ✅ *Implemented.*
   `| a | b |` + `|---|` delimiter + rows. `parse_markdown` emits a `TableBlock`;
   `fill_from_markdown` builds an HWPX table sized to the data and **copies a
   template table's format** (see below). Inline `**bold**` inside cells works.

2. **Complex / merged tables → HTML `<table colspan rowspan>` embedded in Markdown.**
   ⬜ *Next slice.* Keeps a single `.md` → HWPX path; the AI emits HTML for tables
   that need merges, and we build them with `add_table` + `merge_cells`. Humans
   don't hand-edit these in Markdown — they edit in Hangul.

3. **Human-conceived complex tables → drafted in a Sheet (xlsx/GSheet) or HWPX.**
   ⬜ *Strategy only.* When a person designs a complex table first, they hand over a
   Sheet/HWPX draft; the AI fills its values via the existing fill-existing path
   (`ops.form` — `find_cell_by_label`/`fill_by_path`, which already handle merges).

## Format reference (generated tables match the house style)

A generated table is **not** styled with a generic default — it copies the *format*
of a reference table in the template: the table `borderFillIDRef`, the header-row vs
body-row cell border-fill, and the cell paragraph/character styles.

- **Default reference:** the first table in the template (zero markup).
- **Designated reference:** templates often contain many decorative tables (cover,
  TOC), so the author marks the *standard* one by putting a `{{table…}}` token in its
  **caption** — e.g. `표 II-1 {{table_template}}` (any `{{table…}}` form matches). On
  fill the token is stripped from the caption; the table itself stays.

`_find_reference_table()` scans live section trees for a caption token (else the
first table); `_table_format()` reads the reference; `_build_table()` applies it.

### What's copied from the reference, by row band

`_table_format` extracts four border/style zones — **header** (detected by the cell
`header="1"` attribute, not "row 0", so multi-row headers work), **first body row**
(its double top pairs the header's double bottom — one separator, no doubling),
**interior body** (thin), and **last body row** (thick bottom) — plus a hidden
**note** zone. Generated cells reuse the reference's cell style (e.g. JRI_표내용),
header cells are marked `header="1"` (so the header repeats across pages), and the
table copies the reference's `pos` (treatAsChar=0, floating) and `repeatHeader`.

### Caption and note (from the Markdown around the table)

- **Caption title** = the line directly above the table. The generated table clones
  the reference's caption (auto-number + 표제목 style → "<표 N-M> {title}"). The
  table number stays an `autoNum` field. The **chapter number** can't use a cross-ref
  inside a caption, so a `{{chapter_number}}` placeholder in the reference caption is
  substituted with the **explicit `chapter=` / `--chapter`** value (the AI knows the
  chapter): `hwp-agent write c.md --template form.hwpx --chapter 7` (or `--chapter 가`).
  Counting is **best-effort fallback only** — real documents use outline styles too
  inconsistently (chapters not reliably level-0; survey items etc. in outline styles),
  put chapter titles inside tables/boxes, and restart/relabel numbering per section
  (an appendix in A/B/C), so a reliable count isn't feasible; pass the label. Chapter
  precedence: an inline **`{{chapter_number=3}}`** in the caption (forced) >
  `--chapter` > best-effort count.
- **Note/source row** = lines directly below the table starting with `주)` / `출처)`
  / `자료:` (etc.). The note row is merged into one hidden-bordered cell and filled;
  with no such line it's kept empty (invisible). (`merge_cells` leaves covered cells
  behind, so the merge is done by hand: set the anchor `colSpan` and drop the rest.)

Column widths, per-cell border variety, multi-line note paragraphs, chapter-number
*format*, and inline bold inside cells remain approximations / refinements.

> **Known issues from real authoring** (see `docs/author-backlog.md`): the
> `{{table…}}` token is **consumed on each `write` run**, so re-writing a file
> loses it and falls back to the generic table style (item A); generated table
> **widths aren't fit to the text column** and can overflow (item E); cells should
> always use the named table-content style, never 1pt/20pt direct formatting (item F).
