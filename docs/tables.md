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
Column-width distribution and per-cell border variety (in complex references) are
approximated — a representative header/body style is used — candidates for later
refinement.
