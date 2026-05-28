# Section-split appendix — investigation (not yet implemented)

Captured from a skill-testing session (2026-05-24, target: 서귀포 축제 경제효과
분석 보고서 hwpx). Goal: add an **appendix as a new 구역 (section)** after the
references, with **independent outline numbering** (e.g. `A.1.1`). A section
break here means: outline numbering **restarts** (not continued), page numbering
**continues**, and the master page (바탕쪽) is its own (released).

## Status — SOLVED via a Hangul-made empty section (2026-05-24)

The case was completed. The working path is **not** to synthesize a new section
in XML — Hangul **rejects a content-bearing section built from scratch in XML**
(an empty one renders, the same structure with content does not; final cause
never isolated). Instead, **reuse an empty section the user creates in Hangul**:

1. **User, in Hangul:** make an empty 구역 at the appendix spot — 구역 나누기 +
   outline-restart + page-continue + **master-page release** (which auto-creates
   the dedicated empty master page a new section needs; reusing another section's
   master page is what made Hangul drop XML-built sections). Type a `{{appendix}}`
   token into that empty paragraph.
2. **Re-inject the table-format token:** put `{{table_template}}` in the
   reference table's caption (see backlog item **A** — the token is consumed by a
   prior `author` run, so a once-authored file has lost it).
3. `hwp-agent write appendix.md --template BASE.hwpx -o OUT.hwpx` — the appendix
   fills in at the `{{appendix}}` marker, which is **consumed** (removed) on fill.
4. Post-process (the C/D/E/F fixes below). The `{{appendix}}` token no longer
   needs blanking by hand — `author` removes the marker paragraph itself.

So `author` already does the core fill; the remaining gap is **(a)** robustness
fixes that the manual post-processing revealed (see `docs/author-backlog.md`) and
**(b)** *optionally* automating the empty-section skeleton — but the
Hangul-empty-section path is the safest and is the recommended design.

## HWPX requirements for a new section (reverse-engineered)

Verified by diffing the original section-transition and a *known-good empty
section the user created in Hangul*:

1. **New `Contents/sectionN.xml`**, root `<hs:sec>`.
2. First paragraph's first run carries `<hp:secPr>…</hp:secPr>` **immediately
   followed by `<hp:ctrl><hp:colPr …/></hp:ctrl>`** (required).
3. **Register the section in two places:**
   - `Contents/content.hpf`: an `<opf:item>` **and** an `<opf:itemref>` in the
     `<opf:spine>`.
   - `META-INF/container.rdf`: a `hasPart` + a `SectionFile` Description pair.
   - (Master pages are *not* added to `container.rdf`.)
4. **A dedicated master page is mandatory — the biggest trap.** A new section's
   `secPr` must **not reuse another section's master page**, or Hangul drops the
   section. Create a new `Contents/masterpageM.xml` (empty, EVEN type), register
   it in the `content.hpf` manifest, and reference it from `secPr` with
   `masterPageCnt="1"` + `<hp:masterPage idRef="masterpageM"/>`. (The user's
   "바탕쪽 해제" produces exactly such a dedicated empty master page.)
5. **Section properties:** outline numbering *not* continued (a dedicated
   numbering definition + `outlineShapeIDRef`); page numbering continued
   (`secPr startNum`).
6. **`A.1.1` auto-numbering:** add a new `<hh:numbering>` to `header.xml` — level
   1 = `LATIN_CAPITAL` (A, B, C), lower levels `^1.^2`. (The template already
   defines `ROMAN_CAPITAL` and `LATIN_SMALL`, so `LATIN_CAPITAL` is presumed
   valid — not yet verified in Hangul.)

## Notes

- XML-built content sections never rendered in Hangul (empty ones did); rather
  than chase that, the design reuses a Hangul-made empty section. Final cause TBD.
- Side note (not the cause): `author` collapses every section root from ~15
  namespaces to 2 (`hp`, `hs`), but the body still renders, so that alone is fine.
- On re-save, Hangul overwrites our injected numbering definition (id=3, `A.1.1`)
  with its GUI default — consistent with "outline numbering not continued".
- **Ghost "A."**: a leftover empty outline-level-0 paragraph the user made as a
  sample in the empty section will consume "A." first, so the body starts at "B.".
  Neutralize/remove that empty outline paragraph (set to NONE) after filling.

## The remaining ask

(a) The robustness fixes the manual post-processing exposed — tracked in
`docs/author-backlog.md` (items A–F); (b) *optionally* a `--appendix` helper that
emits the empty-section skeleton, though the Hangul-empty-section path is
preferred; (c) user-specified outline-number format (e.g. `A.1.1`) — item D.

This is the concrete, evidence-backed version of the 구역 handling noted in
`docs/strategy.md` ("Later: 구역 (section) handling").
