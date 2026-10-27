# Extracting HWPX → Markdown

Read this when reading an HWPX back as Markdown (`hwp-agent extract`).

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
