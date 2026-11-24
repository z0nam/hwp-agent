# Cover / colophon metadata (표지·판권)

Read this when filling a report's 표지·판권 (`--meta`, front matter, `"meta"` in a `build` manifest).

The baked JI house forms (`examples/house-forms/{정책,전략,기반,센터}과제_서식.normalized.hwpx`)
carry `{{title}}`·`{{report_number}}`·`{{lead_researcher}}`·`{{pub_date}}`·`{{isbn}}`…
on the 표지 and 판권. `build` (and `write`) fill them after the body is placed,
from `"meta": "meta.json"` in the manifest, `--meta meta.json`, or a `---` front
matter block at the top of a Markdown file (stripped from the body; flat
`key: value`, `key:` + `- item` lists, `key: |` blocks — Korean keys like `제목`,
`연구책임`, `공동연구`, `발행일` work too):

```json
{
  "title": "제주 관광 수요 전망을 위한\n기초 연구",
  "report_series": "미래기획연구",
  "report_number": "2026-01",
  "lead_researcher": "홍길동  제주연구원 연구위원",
  "co_lead": [],
  "co_researchers": ["김갑을  제주연구원 연구위원", "이병정  제주연구원 위촉연구원"],
  "pub_date": "2026-08",
  "end_date": "2026-08-31",
  "printer": "○○인쇄",
  "isbn": "979-11-00000-00-0   00000"
}
```

- `\n` in the title is a line break on the cover. `pub_date` `2026-08` → `2026년 8월`.
- 앞표지 shows 연구책임 + 공동책임 (`co_lead`) names only (2026 서식); 판권 lists
  `co_lead` then `co_researchers`, one line each (spacer lines above are removed so
  판권 stays one page). `authors` overrides the cover line.
- `publisher` (발행인) defaults from `end_date` — the 원장 on the project's end date
  (≤2026-10-05 유영봉, ≥2026-10-06 강창민; source `agent-rules/docs/ji-facts.md`).
- `report_series` defaults to the form's own series (`{{report_series:전략연구}}`).
- Missing values: with metadata given, an empty slot is **blanked** (never left
  as `{{…}}`) and missing required ones (title, report_number, lead_researcher,
  pub_date, publisher, isbn) are listed in a warning. With **no** metadata at all,
  the slots stay for a later `form fill` and one warning names them.
- `content.hpf` 문서 정보: `opf:title` ← meta title (else the Markdown's first
  heading); `creator` ← meta `creator`, else the 연구책임 name, else `--author`,
  else the `name` in `~/.config/hwp-agent/profile.json` (nothing else is read
  from the profile — personal data is never cover metadata).
