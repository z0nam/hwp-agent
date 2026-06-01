# PoC plan — AI fills an HWP/HWPX form

## Goal

Hand the toolkit a **form** (an HWP/HWPX template) and a **content intent**, and
have an AI fill the form *in its native format*, preserving layout. The AI reads
the form's fillable structure, maps content to slots, and applies edits; the
toolkit provides deterministic discovery + fill primitives and verification.

## Architecture (the "ABC" layers)

| Layer | What | Module / API | Status |
|------|------|--------------|:--:|
| Foundation | HWP→HWPX conversion + reliable open/save | `hwp_agent.convert`, python-hwpx | ✅ done |
| **A. Understand** | discover fillable slots in a form | `ops.form.analyze_form` (placeholders + empty label cells) | ✅ first cut |
| **B. Fill** | set slot values, preserve formatting | `ops.form.fill_form` (placeholder + cell path); `ops.metadata` | ✅ first cut |
| **C. AI interface** | expose slots to an AI, accept a fill map | CLI `form analyze --json` / `form fill --map`; Python API | ✅ first cut (CLI/JSON) |
| Verify | round-trip / structural integrity of edits | `ops.verify` | ⬜ M2 |

The AI-driven loop today: `form analyze --json` → (AI maps content → slots) →
`form fill --map`.

## What's shipped (first cut, 2026-05-23)

- `analyze_form`: finds `{{placeholder}}` tokens and empty label→neighbour table
  cells; returns a structured `FormSpec` (JSON via CLI).
- `fill_form`: fills by placeholder name, discovered empty cell, or explicit
  cell path (`"label > right"`); reports `filled` / `missing`.
- `ops.metadata`: read/fill document properties (title, creator, …).
- CLI: `hwp-agent form analyze|fill`, `hwp-agent meta`.
- Verified end-to-end on a real 2.7 MB institutional document.

## Known limitations → hardening backlog (M2)

- **Label-cell field detection is noisy on filled documents** (every empty
  neighbour reads as a slot). Tuned for blank *templates*; needs a heuristic
  (form-like tables, label shape) to be precise on arbitrary docs.
- **Cell fill appends** rather than overwrites when the target is non-empty
  (clean SET only on blank/template cells). Need true overwrite semantics.
- **Placeholders must sit within one run** (split-run tokens are missed).
- No support yet for **repeated-row tables** (line items) or removing/adding rows.
- No **verification** that an edit preserved structure/formatting.

## Milestones & proposed schedule

Dates are proposals from 2026-05-23; adjust freely.

- **M1 — form-fill first cut** ✅ *(done, 2026-05-23)*
  analyze + fill + CLI/JSON + metadata, validated on a real document.

- **M2 — fill hardening** *(target: ~2026-05-30)*
  Precise label-cell detection; overwrite/SET semantics; multi-run placeholders;
  `ops.verify` (reopen + structural diff + non-BMP/U+FFFD guard). Tests on a
  blank institutional template.

- **M3 — AI interface as Skill/MCP** *(target: ~2026-06-06)*
  Wrap analyze→fill→verify as a Claude Code Skill and/or MCP server so an agent
  drives the loop conversationally (form in → filled doc out). Round-trip the
  result through Hangul.

- **M4 — PoC demo** *(target: ~2026-06-13)*
  End-to-end: give the AI a real blank form + content brief; it produces a filled
  HWPX, verified visually in Hangul. Capture fidelity gaps in `findings.md`.

- **M5 — Windows / cross-platform support** *(unscheduled; needed for in-institution use)*
  The institution runs Windows, so the toolkit must work there. The **core CLI is
  already portable** — pure Python on pathlib, `subprocess.run([...])` (no shell),
  `shutil.which("java")` (resolves `java.exe`); lxml & python-hwpx ship Windows
  wheels. The gaps are the POSIX-only periphery:
  1. **Installer/build scripts are bash** (`install.sh`, `bootstrap.sh`) — add a
     PowerShell installer (`install.ps1`) or document running under Git Bash / WSL;
     `bootstrap.sh`'s Maven build needs a Windows path too (or ship a prebuilt jar).
  2. **Skill registration uses a symlink** (`ln`) — on Windows, *copy* the skill
     folder into `%USERPROFILE%\.claude\skills\` instead (symlinks need dev mode).
  3. **Distribute the converter jar** so Windows users needn't build it (JDK/Maven
     on Windows is a high bar) — e.g. attach it to a GitHub release.
  4. **Verify on Windows**: convert (Java present), the editing ops, and a Hangul
     round-trip; fix any path/encoding (cp949 vs utf-8) surprises.

- **M6 — HWPX → Markdown extraction** ◑ *(v1 shipped 2026-06-01; HTML-table & inline-emphasis variants deferred)*
  The inverse of `write`: read an HWPX and emit **body-focused Markdown**.
  Shipped as `hwp-agent extract FILE.hwpx [--body-only] [-o OUT.md]` + `ops.extract`
  module. Scope of v1:
  1. **Headings / bullets / ordered / body** roundtrip from the role map (and the
     paragraph-property `<hh:heading>` as a fallback when no role is set).
  2. **Tables: every table is rendered as Markdown pipe**, with **merged cells
     flattened Excel-style** (value duplicated into every covered position). Data
     preserved, merge intent lost — the doubled-value pattern signals merges.
     Multi-paragraph cells use `<br>` to keep paragraph breaks readable.
  3. **`--body-only`** skips everything before the first level-1 heading (cover,
     TOC).
  Deferred: inline emphasis (run-level **bold**/*italic* detection), image bytes
  (figures emitted as caption text only), complex-table HTML variant.
  - **North-star use case:** a draft or **non-conforming HWP** (written without
    following the house form) → extract to Markdown → re-`write` it onto a proper
    template's styles, i.e. *make a messy draft conform to the form*. This closes
    the loop: messy HWP → MD (intent) → styled HWPX. Until then, type-2/3 docs
    (weak/flat) are handled in place; M6 is the "rebuild it cleanly" path.

- **M7 — usable from other assistants (Claude.ai chat / ChatGPT / Codex)** *(unscheduled)*
  Today the surface is the Claude Code skill + CLI. Broaden it so the same ops
  drive from other agents. Builds on the MCP server (M3). Two tiers, by how the
  client runs tools:
  1. **Local-process clients (Codex, Claude Code/Desktop)** — speak MCP over
     **stdio**; the same local MCP server works. Mostly a config/docs task
     (publish the server command + an mcp.json snippet per client).
  2. **Web chat clients (claude.ai, ChatGPT)** — *cannot spawn a local process*,
     so they need a **remote (HTTP/SSE) MCP server** (a "connector"), which means
     **hosting + auth + file upload/download** (the HWP comes in as an upload, is
     processed server-side with the jar, and the result is handed back). ChatGPT
     without MCP can fall back to a **custom GPT with Actions (OpenAPI)** over the
     same hosted endpoints. Note: claude.ai can also load uploaded *Skills*, but a
     skill alone can't execute the CLI/jar in web chat — it still needs the hosted
     backend.
  - **Open issues:** hosting/runtime for the Java converter, auth, file-size and
    privacy limits (institutional documents leaving the building), and keeping one
    ops core behind both the CLI and the (local + remote) MCP surfaces.

- **M8 — section-split appendix (구역 분리 부록)** *(case SOLVED; productize)*
  Add an appendix as a **new 구역** with independent `A.1.1` numbering. Resolved
  not by synthesizing the section in XML (Hangul rejects content-bearing XML-built
  sections) but by **filling an empty section the user makes in Hangul** + a
  `{{appendix}}` token; `author` already does the fill. See `docs/section-split.md`.
  Productizing = the `author` hardening fixes that the manual post-processing
  exposed — **`docs/author-backlog.md`** (items A/C/E/F are general `author` bugs;
  B/D are the section/numbering helpers) — plus an optional `--appendix` skeleton.

- **`author` hardening backlog** *(rolling; see `docs/author-backlog.md`)*
  Six concrete defects/improvements from real authoring: A `{{table_template}}`
  token consumed (format regression), C missing `linesegarray` (Hangul demotes
  headings), E table width not fit to the text column, F oversized/1pt direct
  formatting instead of named styles, B/D section-split & custom outline numbering.

## Open questions

- Slot identity for ambiguous forms (duplicate labels, nested tables).
- How much the AI infers vs. an explicit slot contract (`{{}}` vs. label cells).
- Packaging surface: Skill vs. MCP vs. both; how the form + brief are passed.

## Next up (as of 2026-06-01)

Recently shipped (pushed): `image list`/`replace`, **`write`** (renamed from
`author`), `{{appendix}}` insertion marker + section-`secPr` preservation, `---`
→ 가로선, heading manual-number stripping, friendly missing-file errors,
manifest-fallback log note, contextual heading spacing, **`check`** (renamed from
`doctor`), **LaTeX-style cross-references** (`{label:id}`/`{ref:id}`), caption
framing auto-strip, HTML comment stripping, **`extract`** (HWPX → body-focused
Markdown with merged-cell flattening — **M6 v1**). Old command names (`author`,
`doctor`) are kept as aliases.

**Top priority — make 제주연구원's official template machine-friendly.** Two tracks:

1. **Golden template (one-time, in Hangul).** Run `hwp-agent check` on the
   official .hwpx; close the gaps it reports — give every structural element a
   **named style** + `AI:<ROLE>` (esp. bullets: `AI:BULLET_n`, item **G**), fix the
   **font hierarchy** (deeper ≤ shallower), add `{{body}}`/`{{appendix}}` markers,
   `{{table_template}}` on a reference table, and `AI:INSTRUCTION` guidance.
   Iterate until `check` is clean → the canonical template every report starts from.
2. **Tooling.** Grow `check` from *diagnosis* into a **fix-checklist / machine-
   readiness score**. This track is where deferred items land: **F** (whole-line
   bold → named sub-heading style) and **G** (bullet glyph/size order fallback when
   no `AI:BULLET_n` is declared).

**Queued after:** Hangul render-verify of items **C/F**; **M3** MCP server; **M5**
Windows; **M6 v2** (inline emphasis + HTML-table variant + figure bytes); **M7**
other assistants. (See milestones above and `docs/author-backlog.md`.)
