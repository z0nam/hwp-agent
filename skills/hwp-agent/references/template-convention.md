# Machine-friendly HWPX template convention

A convention for marking up an HWPX template so an AI knows what each part is for.
It is **structure-first, naming-second**: most roles are inferred from the data the
document already carries; explicit names only *override* or *add* roles. A plain
type-1 document works with **zero** extra markup.

## Style roles

`ops.styles.role_map(doc)` returns a map of **role → style id**. Roles:

| Role | Meaning | How it's detected |
|------|---------|-------------------|
| `HEADING_1` … `HEADING_n` | outline headings (auto-numbered) | a style whose paragraph-property heading is `OUTLINE level=N` → `HEADING_{N+1}` |
| `BULLET_1` … `BULLET_n` | bullet list levels | heading `BULLET level=N` → `BULLET_{N+1}` |
| `BODY` | normal body text | `engName="Normal"` / `name="바탕글"` |
| `TITLE`, `CAPTION_TABLE`, … | optional named roles | `AI:<ROLE>` naming only |
| `INSTRUCTION` | directions to the AI (not content) | `AI:INSTRUCTION` naming only |

### Auto (no markup needed)

Outline level lives in the data (`<hh:heading type="OUTLINE" level="N">`), so any
template that uses real heading styles is understood as-is. When several styles share
a level, the canonical `engName="Outline N"` wins, then most-used, then lowest id.

### Naming override (opt-in)

A style whose **name or engName** matches `AI:<ROLE>` claims that role outright,
e.g. `AI:H1`, `AI:BODY`, `AI:CAPTION_TABLE`, `AI:INSTRUCTION`. Use this to label
intent unambiguously, or to mark roles that have no structural signal (instructions,
captions, title slots).

## Passing instructions to the AI

Two channels, both visible and editable inside Hangul:

- **Instruction paragraphs** — write directions in a paragraph styled with an
  `AI:INSTRUCTION` style (e.g. "여기에 3문단으로 배경을 서술"). `read_instructions`
  returns their text; `fill_from_markdown` **removes** them from the output.
- **Slots / fill positions** — `{{slot}}` tokens (e.g. `{{title}}`, `{{author}}`)
  mark where specific values go; discovered by `ops.form` and filled by name. A
  `{{body}}` or `{{appendix}}` token (its own paragraph) is an **insertion
  marker**: `{{body}}` marks **where the main body begins** (the start of 본문 /
  chapter 1, after 표지·목차), `{{appendix}}` marks **where an appendix begins**.
  `fill_from_markdown` inserts the authored content starting at that point and
  **consumes** (removes) the marker; it defines a start boundary, not a generic
  "fill here" hole. Without a marker, content is appended to the last section. If a
  template has both, the first in document order is used.
  A `{{table…}}` token (e.g. `{{table_template}}`) in a table's **caption** marks
  that table as the **format reference** for generated tables; the token is stripped
  from the caption on fill (the table stays). Without it, the first table in the
  template is the format reference. A `{{chapter_number}}` token in that caption is
  replaced with the `--chapter` value; `{{chapter_number=3}}` **forces** a value
  inline (wins over `--chapter`) as a worst-case override. See `docs/tables.md`.

## Authoring model (Markdown, first cut)

The AI writes Markdown; it is projected onto the template's styles:

| Markdown | Role | Result |
|----------|------|--------|
| `# H1` / `## H2` / `### H3` | `HEADING_n` | template outline style → Hangul auto-numbers it |
| `1.` / `2.` (indent = nesting) | `ORDERED_n` | template's numbered-list style → auto "1. 2. 3." |
| `- item` (indent = nesting) | `BULLET_n` | template bullet style |
| `\| a \| b \|` + `\|---\|` | (table) | HWPX table copying a template table's format (see `docs/tables.md`) |
| paragraph text | `BODY` | 바탕글/Normal |
| `**bold**` / `*italic*` | (run) | separate run in the same font family's bold/italic |

Headings map by absolute level (`#`→`HEADING_1`), clamped to the deepest defined.
Bullets/ordered lists map by **rank** — the shallowest Markdown depth → the
shallowest available `BULLET_n`/`ORDERED_n` (templates often start lists at outline
level 1, not 0). `ORDERED_n` is detected from enumerator-named outline styles
("1.", "1)", "가."). With no matching list style, items fall back to `BODY`.

### Font-size hierarchy principle

Font size must be **monotonically non-increasing as the hierarchy deepens** — a
deeper level's text is smaller than, or at most equal to, its parent's, never
larger. Think LaTeX `section` ≥ `subsection` ≥ `subsubsection` ≥ `paragraph` ≥
body. This holds across the whole ladder (Heading 1 ≥ Heading 2 ≥ … ≥ Body, and
list/그 하위 levels likewise). A deep heading (a `####` / paragraph-level item)
rendered **larger** than its parent looks badly broken — it's a hard rule, not a
preference.

For type-1 we *reuse* the template's styles, so the template already encodes the
sizes and we don't set them — but this is the rule to (a) **verify** (a future
`ops.verify` check: flag a template/style system where a deeper level is larger),
and (b) **honor when we assign sizes ourselves** — type-2/3 inference and the M6
"rebuild cleanly" path (`docs/poc-plan.md`).

### Named styles over direct formatting

**Anything structural gets a named style; only in-sentence emphasis uses a
run-level override.** A sub-heading, a note, a body variant — give it a *style*
(reuse one, or define a new one in the template, authored once in Hangul). The
**only** thing that should ride on direct character formatting is *partial,
in-sentence emphasis* (a few bold/italic words inside a sentence), as a run-level
override of an otherwise styled paragraph.

**Why:** a human editor can then restyle *every* sub-heading (or note, or body
paragraph) at once by editing the style — impossible if each was ad-hoc
direct-formatted. Direct formatting on 바탕글/Normal is the anti-pattern that
produced the 1pt / 20pt cell bugs (`docs/author-backlog.md` items F, 6).

Implication for authoring: when a structural element has **no** matching style in
the template, the answer is to **define the style**, not to direct-format Normal.
(Consistent with the template principle: structure lives in the template,
authored once.) Plain `BODY` is itself a named style and is fine for ordinary
paragraphs — the rule is *don't layer direct formatting on top of it to fake a
sub-heading*.

## Example

A minimal type-1 template needs no markup at all — just real heading styles. To add
guidance, the author inserts (in Hangul):

```
[paragraph in style "AI:INSTRUCTION"]  배경 → 현황 → 제언 순서로 작성. 표는 '표 N' 캡션 사용.
{{title}}                              ← filled via `hwp-agent form fill`
{{body}}                               ← authored Markdown lands here
```

Then: `hwp-agent classify form.hwpx` → `structured`; `hwp-agent styles form.hwpx`
shows the role map; `hwp-agent write content.md --template form.hwpx -o out.hwpx` fills it.
