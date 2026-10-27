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

- **As a Claude plugin** (Claude Desktop Cowork/Code tab, Claude Code): the
  plugin starts the `hwp-agent` MCP server itself (needs `uv` on PATH). Prefer
  its tools (`convert_hwp_to_hwpx`, `analyze_form_slots`, `fill_form_slots`,
  `extract_to_markdown`, …) when they cover the step.
- **CLI** (Codex, Hermes, terminal): `hwp-agent` on PATH. Verify with
  `hwp-agent --version`. Install with `uv tool install git+https://github.com/z0nam/hwp-agent` (pin a release with `...hwp-agent@v0.2.0`; not on PyPI)
  or the Windows installer `hwp-agent-setup.exe`.
- `.hwp` → `.hwpx` conversion needs Java 17+ and the converter jar. Fetch the jar
  once with `hwp-agent setup` (the Windows installer bundles both). Editing an
  existing `.hwpx` needs neither.

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
   - **Form/slot fill** (fixed fields: dates, names, table cells with labels) or
     **profile auto-fill** (the same person's standing data across many forms) —
     read `references/form.md` first (slot keys, `--grid` cell addresses, row
     restructuring, `--profile`).
   - **Free authoring** (writing report body content) — write Markdown, then
     `hwp-agent write content.md --template work.hwpx -o out.hwpx`
     (positional = the Markdown you're writing; `--template` = the .hwpx to fill).

6. **Verify.** Open the output and confirm it's intact: reopen with the tool
   (`hwp-agent meta out.hwpx` round-trips it) and, when possible, have the human
   open it in Hangul. Generated text must be free of `U+FFFD` (�) replacement
   characters.

## Pitfalls

- **Never go through DOCX.** It loses Korean formatting silently.
- **Don't synthesize numbering** (chapter/section/list/table numbers). Reuse the
  template's styles and let HWPX auto-numbering produce them; only the chapter
  *label* for captions is supplied by you (`--chapter`).
- **Don't hand-edit `.hwpx` XML** unless you know it; prefer the CLI.
- **Inspect first.** Run `classify` → `styles` → `instructions` before authoring.
- This flow is solid for **structured** templates; be cautious on `weak`/`flat`.

## Which file to read

This file holds the common rules. Before a step below, read the file for it (paths are relative to this skill's directory). If a file cannot be read, say so and stop — do not guess its rules.

| when | read |
|---|---|
| filling a form: slots, `--grid` cell addresses, row restructuring, `--profile` | `references/form.md` |
| exact options of any command | `references/commands.md` |
| generating Markdown for `write` / `build` (headings, lists, tables, template tokens, cross-refs, table-reference pause) | `references/write.md` |
| … with `[^id]` footnotes, links or URLs | + `references/write-notes-links.md` |
| … with `$…$` / `$$…$$` math, or `hwp-agent eq` | + `references/write-equations.md` |
| … with image lines `![alt](path)` | + `references/write-figures.md` |
| filling 표지·판권 (`--meta`, front matter) | `references/cover-meta.md` |
| reading HWPX back as Markdown (`extract`) | `references/extract.md` |
| listing or replacing figure images (`image list` / `image replace`) | `references/images.md` |
| editing HWPX XML by hand (flat forms `form fill` can't target) | `references/hand-edit.md` |
| table format strategy, what is copied from the reference table | `references/tables.md` |
| marking up a template itself: the full machine-friendly convention (`AI:` styles, `{{…}}` tokens). Not needed to `write` into a template — `references/write.md` has the tokens a writer meets | `references/template-convention.md` |
| rendering to PDF/DOCX, layout engines (`pdf`, `docx`, `verify`, `fit tails`) | the `pdf` / `fit tails` rows of `references/commands.md` |
