---
name: hwp-author
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
   This skill's `author` flow targets **structured** templates (a real outline
   style system). For `weak`/`flat`, fall back to form-fill or ask the human.

3. **Read the style roles** the template exposes (role → style id):
   `hwp-agent styles work.hwpx` (add `--json` for machine use). Roles include
   `HEADING_1..n`, `BULLET_n`, `ORDERED_n`, `BODY`. You don't set styles
   yourself — you write Markdown and the tool projects it onto these.

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
     `hwp-agent author work.hwpx --md content.md -o out.hwpx`.

6. **Verify.** Open the output and confirm it's intact: reopen with the tool
   (`hwp-agent meta out.hwpx` round-trips it) and, when possible, have the human
   open it in Hangul. Generated text must be free of `U+FFFD` (�) replacement
   characters.

## Writing Markdown for `author`

The AI writes Markdown; the tool maps it onto the template's styles:

- `#`/`##`/`###` → the template's Heading 1/2/3 (outline numbering comes for
  free — never type "1." / "1.1" yourself).
- `- ` → bullet styles; `1. ` → ordered styles; plain lines → Body.
- `**bold**` / `*italic*` inline emphasis becomes runs.
- `| a | b |` + `|---|` + rows → an HWPX **table** (see below).

### Template tokens (placed in the template, in Hangul, by a human)

- `{{body}}` — its own paragraph, marks where the authored body is inserted.
  Without it, content is appended to the last section.
- `{{table_template}}` (any `{{table…}}` form) in a **table's caption** marks
  that table as the **format reference** — generated tables copy its borders,
  cell styles, header look, and geometry. Without it, the first table is used.
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

## Command reference

| command | purpose |
|---|---|
| `hwp-agent convert IN.hwp OUT.hwpx` | HWP → HWPX (needs the jar) |
| `hwp-agent classify FILE.hwpx` | structured / weak / flat |
| `hwp-agent styles FILE.hwpx [--json]` | machine style roles (role → style id) |
| `hwp-agent instructions FILE.hwpx [--json]` | AI:INSTRUCTION directions + `{{slots}}` |
| `hwp-agent form analyze FILE.hwpx [--json]` | list fillable slots |
| `hwp-agent form fill FILE.hwpx --set K=V [-o OUT]` | fill slots by name |
| `hwp-agent author FILE.hwpx --md C.md [--chapter N] [-o OUT]` | author from Markdown |
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

## Deeper reference

- `references/template-convention.md` — full machine-friendly template convention.
- `references/tables.md` — the tiered table strategy and what's copied from the
  reference table.
