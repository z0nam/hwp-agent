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
  `{{body}}` token (its own paragraph) marks where the authored Markdown body is
  inserted; `fill_from_markdown` places the content there and removes the marker.
  Without a `{{body}}` marker, content is appended to the last section.
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

## Example

A minimal type-1 template needs no markup at all — just real heading styles. To add
guidance, the author inserts (in Hangul):

```
[paragraph in style "AI:INSTRUCTION"]  배경 → 현황 → 제언 순서로 작성. 표는 '표 N' 캡션 사용.
{{title}}                              ← filled via `hwp-agent form fill`
{{body}}                               ← authored Markdown lands here
```

Then: `hwp-agent classify form.hwpx` → `structured`; `hwp-agent styles form.hwpx`
shows the role map; `hwp-agent author form.hwpx --md content.md -o out.hwpx` fills it.
