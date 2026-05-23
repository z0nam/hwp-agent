# Strategy — three document classes, type-1 first

## The goal, restated

Hand the toolkit an HWP/HWPX **form/template** and a **content intent**; an AI fills
the document *in its native format*, preserving layout. The toolkit gives the AI a
deterministic view of the document's structure and deterministic edit primitives;
the AI maps content onto the structure.

## Three classes of real-world documents

The documents we encounter fall into three classes, by how much machine-usable
structure they carry:

1. **Well-structured** — a real style system. Heading 1/2/3 are bound to outline
   numbering (1 / 1.1 / 1.1.1), tables/figures auto-number, cross-references point
   at those numbers. The document's structure is *in the data*. **Easiest to drive.**
2. **Weak styles** — styles are assigned, but the hierarchy is typed by hand
   ("1장", "2절"). The numbers are literal text, not generated. Requires *inferring*
   structure from a mix of styles and text patterns.
3. **No styles** — everything sits in 바탕글/Normal; indentation is whitespace,
   bullets are typed characters. Structure must be *guessed*; edit error rate is high.

## Why type-1 first

For type-1, authored content (Markdown today) maps cleanly onto the template's own
styles: `#`→Heading 1, `##`→Heading 2, bullets→bullet styles, text→Body. Because we
**reuse the template's existing style ids**, outline numbering, fonts, and spacing
come for free — we never synthesize numbering. This is the lowest-risk, highest-value
starting point, and it establishes the role-map + authoring machinery the later
classes build on.

The direction for type-1: treat a Markdown/LaTeX-style document model as the AI's
authoring frame, and *project it onto the template's styles*. Most of the work
collapses to a structure→style mapping.

## Roadmap

- **Type-1 (now):** detect the style system, expose a **role map** (role → style id),
  fill from Markdown using the template's outline styles. Modules: `ops.styles`
  (`role_map`, `classify_document`), `ops.author` (`fill_from_markdown`). The
  machine-friendly template convention is specified in `docs/template-convention.md`.
- **Type-2 (next):** infer hierarchy where styles are weak — combine style signals
  with text patterns ("1장", "가."), and reconcile manual numbers with outline levels.
- **Type-3 (later):** best-effort on flat documents; surface low confidence and ask
  for human confirmation. Higher error rate, so guarded.

`classify_document` returns `structured` | `weak` | `flat`, gating which strategy a
given document gets.

## How it plugs into the PoC

This sits inside Phase 2 (AI-driven editing) of `docs/poc-plan.md`: `analyze`/`fill`
(form slots) + `styles`/`classify`/`author` (structure) are the primitives; the AI
loop is *classify → read styles/instructions → author Markdown → fill → verify*,
eventually packaged as a Claude Code Skill / MCP server.
