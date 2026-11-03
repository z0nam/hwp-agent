# Figures in `write` Markdown

Read this when the Markdown has an image line `![alt](path)`.

## Figures — `![alt](path){#fig_X}`

An image on a line of its own becomes a figure: a **1-column, 2-row borderless
table** with the picture on top (scaled to the text width, aspect kept, capped at
80% of the page height) and a `[그림 {chapter}-N] alt` caption on top.

```markdown
![연도별 핵심 지표 추이](assets/trend.png){#fig_trend}
출처: 제주연구원 내부 자료(2026)
주: 2026년은 잠정치임
```

- The `출처:` / `주:` / `자료:` lines **directly under** the image line (no blank
  line) go in the lower cell, one paragraph each, in the template's note style.
- Figures are numbered in their **own** sequence, never as tables; `{ref:fig_trend}`
  → `그림 Ⅳ-1`. The anchor is optional (no anchor = numbered, not referable).
- Write only the title as alt text — the `[그림 …]` framing is added. Links and
  `**bold**` in it work as in table captions.
- Paths are relative to the Markdown file (`write`) or to each part file
  (`build`, falling back to the manifest folder). PNG/JPEG/GIF/BMP (TIFF: convert first). A
  missing image keeps the figure (number + caption) with a `[그림 없음: path]`
  placeholder and a warning.
- The caption style comes from the template's `{{figure_template}}` figure (its
  picture is never copied into the output), else from the table caption style.
- `extract` turns these figure tables back into `![alt](path){#fig_X}` + notes.
