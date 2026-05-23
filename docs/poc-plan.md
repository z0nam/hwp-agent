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

## Open questions

- Slot identity for ambiguous forms (duplicate labels, nested tables).
- How much the AI infers vs. an explicit slot contract (`{{}}` vs. label cells).
- Packaging surface: Skill vs. MCP vs. both; how the form + brief are passed.
