# Equations (수식) in `write` Markdown

Read this when the Markdown has `$…$` / `$$…$$` math, or for `hwp-agent eq`.

## Equations (수식)

LaTeX math becomes a real Hangul equation object (수식 편집기로 더블클릭 편집 가능).
Hangul does not read LaTeX, so it is converted to Hangul's own script
(`\frac{a}{b}` → `{a} over {b}`, `\sum_{t=1}^{n}` → `sum _{t=1} ^{n}`, …).

```markdown
모형은 $y_t = \mu + \varepsilon_t$ 로 둠.

$$
\Phi(B^s)\phi(B)(1-B)^d y_t = \Theta(B^s)\theta(B)\varepsilon_t
$$
```

- `$…$` inside a line → inline equation (body text and table cells). A `$$…$$`
  block on its own line(s) → its own paragraph (template 「수식」 style if any,
  else body).
- Prices are not math: pandoc rules (no space just inside the `$`, closing `$`
  not followed by a digit) — `$5 and $10` stays text; `\$` is a literal dollar.
- Upright text (`\text`, `\mathrm`, `\operatorname`) is written `rm … it`:
  Hangul's `rm` is a mode switch that would otherwise turn every following
  variable upright. Never `{rm …} _{x}` — Hangul 2022 **crashes** on a brace group
  holding `rm` followed by a sub/superscript (the converter strips such braces or
  warns; mind it when writing `\hwp{…}` by hand).
- No Hangul equivalent → approximated **with a warning**: `\overset{a}{b}` →
  `b ^{a}`, `\underset`, `\underbrace`, `\mathcal`. Unknown commands are written
  as upright names and warned. Read the warnings and fix the source if needed.
- Escape hatch: `$\hwp{…}$` passes Hangul equation script through untouched.
- `hwp-agent eq '<latex>'` prints the script only (붙여넣기용 — 한글 수식 편집기의
  스크립트 창에 붙여넣기). `--no-math` (write/build) keeps math as text;
  `build` manifests take `"math": false`.
- `extract` writes equations back as `$\hwp{script}$` (`$$…$$` when the paragraph
  is only the equation), so an HWPX ↔ Markdown round trip keeps them. `check`
  reports `equations: N object(s), M LaTeX span(s) left as text`.
- Box size: Hangul keeps the stored size of an equation until it is edited, so
  `write` estimates it (fitted to 130 Hangul-laid-out equations). If a formula
  ever overlaps the next word in Hangul, double-click it and close the editor —
  Hangul re-measures it.
- Writing `\hwp{…}` by hand: quote words inside `rm … it` (`rm "argmin" it` —
  unquoted, Hangul splits `argmin` into garbage); group an accent before a
  sub/superscript (`{hat {y}} _{t}`); there is no `=>`/`forall` — use `RARROW`,
  `∀` (Unicode symbols are fine).
- Preview caveat: rhwp's equation renderer is rough (`<=` drawn literally, `∥` as
  `//`); Hangul / `hwp-agent pdf --engine hwp2pdf` is authoritative (verified there:
  fractions, limits, matrices, cases, aligned, accents, Greek, ∥, ℝ, ⇒, ∀).
