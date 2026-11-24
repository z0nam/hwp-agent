# Footnotes and hyperlinks in `write` Markdown

Read this when the Markdown has `[^id]` footnotes, links or bare URLs.

## Footnotes (각주)

Use Markdown footnotes for 신문기사·보도자료·온라인 자료 출처 (JRI 인용 지침: 각주
원칙). The reference becomes a Hangul footnote control in place; the note text
uses the template's 「각주」 style (body style if none) and the section's
`footNotePr` numbering (`1)`, `2)`, … continuous).

```markdown
* 도민 인식이 개선됨[^nocut]

[^nocut]: 노컷뉴스, 「제목」(입력: 2026.10.7. 13:52)
    들여쓴 다음 줄은 같은 각주로 이어짐.
```

- Definitions may sit anywhere (usually at the end); they are removed from the
  body. References work in body text **and table cells**.
- Referencing the same id twice makes two notes (Hangul has no re-reference).
- A `[^id]` with no definition stays literal and a definition nobody references
  is dropped — both are reported as warnings; fix the typo and re-run.
- `extract` does the inverse (`<hp:footNote>` → `[^n]` + definitions at the end),
  so an HWPX ↔ Markdown round trip keeps footnotes.
- Preview caveat: rhwp draws the notes but may stack multi-note lines tightly
  (no layout cache); Hangul / `hwp-agent pdf --engine hwp2pdf` is authoritative.

## Links (하이퍼링크)

`[표시](url)`, `<url>` and bare `http(s)://` URLs become Hangul hyperlink fields —
in body text, table cells **and footnote notes** (so 각주 출처 URL is clickable).

```markdown
[^law]: 법제처, 「개인정보 보호법」, https://www.law.go.kr/법령/개인정보보호법 (2026.10.15. 접속)
자세한 내용은 [제주연구원](https://www.jri.re.kr) 참조.
```

- A bare URL ends at whitespace / CJK brackets; trailing `.,;:` and a Korean
  particle glued to it (`https://x.kr에서`) are left out; a Korean path segment
  (`/법령/…`) stays in.
- The target needs a scheme — `[x](./a.md)` stays literal. Images `![…](…)` are
  not links.
- Look: the template's 「하이퍼링크」 style if it has one, else the surrounding
  text's look (Hangul does **not** colour links on its own). `--blue-links` draws
  them blue + underlined. `--no-links` keeps URLs as plain text.
- `extract` writes links back as `[표시](url)` (bare URL when text = URL);
  `check` reports `hyperlinks: N field(s), M URL text(s) not linked` and an
  `integrity [field]` finding for a fieldBegin/fieldEnd pair cut by an edit.
- `build` manifests take `"links": false` / `"blue_links": true`.
- Preview caveat: rhwp can draw a footnote mark that follows links in the same
  paragraph at the wrong spot; Hangul places it correctly.
