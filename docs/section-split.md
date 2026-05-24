# Section-split appendix — investigation (not yet implemented)

Captured from a skill-testing session (2026-05-24, target: 서귀포 축제 경제효과
분석 보고서 hwpx). Goal: add an **appendix as a new 구역 (section)** after the
references, with **independent outline numbering** (e.g. `A.1.1`). A section
break here means: outline numbering **restarts** (not continued), page numbering
**continues**, and the master page (바탕쪽) is its own (released).

## Status

- **`hwp-agent author` only appends to the last existing section.** It has no
  "split into a new section" capability.
- Creating a new **content-bearing** section by hand-editing the HWPX XML
  **failed to render in Hangul** even after meeting every requirement below — an
  *empty* new section (made by the user in Hangul) renders fine, but the same
  structure with content does not. Root cause not yet isolated (no Hangul in the
  investigation environment to bisect the final blocker).
- **Interim workaround shipped:** appendix appended into the last section, with
  `부록 A` / `A-1` labels kept manually; the user performs the 구역 나누기 in
  Hangul (outline-restart / page-continue / master-page release).

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

## Open blockers

- A file meeting all of 1–5 (with a dedicated master page) **still doesn't show
  the content-bearing new section** in Hangul; an empty one does. Final cause TBD.
- Side note (not the cause): `author` collapses every section root from ~15
  namespaces to 2 (`hp`, `hs`), but the body still renders, so that alone is fine.
- On re-save, Hangul overwrites our injected numbering definition (id=3, `A.1.1`)
  with its GUI default — consistent with "outline numbering not continued".

## The ask (future feature)

(a) Pin down exactly why a content-bearing new section doesn't render (with real
Hangul verification); (b) design/implement a `--new-section` / `--appendix`
option on `hwp-agent` that adds a section-split appendix; (c) support a
user-specified outline-number format (e.g. `A.1.1`).

This is the concrete, evidence-backed version of the 구역 handling noted in
`docs/strategy.md` ("Later: 구역 (section) handling").
