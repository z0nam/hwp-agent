# When you must hand-edit HWPX (flat forms `form fill` can't target)

Read this before editing HWPX XML by hand.

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
