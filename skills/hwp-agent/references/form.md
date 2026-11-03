# Form / slot fill and profile auto-fill

Read this before `hwp-agent form analyze` / `form fill` (step 5 of the authoring loop in `SKILL.md`).

- **Form/slot fill** (fixed fields: dates, names, table cells with labels) —
  `hwp-agent form analyze work.hwpx --json` to list slots, then
  `hwp-agent form fill work.hwpx --set "신청일=2026-05-24" -o out.hwpx`.
  Slots only cover label cells whose neighbour is empty — if the cell you need
  already holds text, run `hwp-agent form analyze work.hwpx --grid` and fill it
  by address: `--set "cell:0:1:2=..."`
  (or `--map values.json`). Slot keys may be a label (`성명`), a label path
  (`성명 > right` / `> below`), a stable address (`cell:<table>:<row>:<col>`,
  also emitted as `cell_path` by `analyze`), a `checkbox:<label>` set `on`/`off`
  (□↔■), a `tab:<anchor>` inline field, or a `{{placeholder}}`. Fills overwrite
  (true SET), so re-running is safe.
  **Sample table has too few/too many rows?** Restructure it in the same call:
  `--delete-rows cell:0:2-13` (rows of the original table) and
  `--append-rows cell:0:+20` (blank copies of the last row, format kept) run
  *before* `--set`, so `--set` addresses the new rows. To copy a row group
  (e.g. a shaded category row + its detail rows) use
  `--repeat-group cell:0:2-4:+7` (rows 2-4, 7 more copies after row 4). Run
  `form analyze --grid` on the result to see the final coordinates. A
  vertical merge straddling a range boundary is refused — widen the range.
- **Profile auto-fill** (the same person's standing data across many forms) —
  `hwp-agent form fill work.hwpx --profile ~/.config/hwp-agent/profile.json
  --date today -o out.hwpx`. Maps 성명/생년월일/주소/연락처/학력/경력/계좌 from a
  saved JSON (copy `examples/profile.example.json`) onto matching slots; reports
  what was filled vs left blank. Repeated 학력/경력 rows map under their header.
