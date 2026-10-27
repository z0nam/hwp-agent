# hwp-agent

Edit **HWP / HWPX** — the document standard used across Korean public and
research institutions — directly with AI, **without a lossy DOCX round-trip**.

Going through DOCX (the common "just convert it" shortcut) silently drops or
mangles Korean-specific formatting: cover-page layouts, 표(table) styling,
머리말/꼬리말, numbering, fonts. `hwp-agent` works in the native format instead,
so formatting is preserved. The end goal is to let an AI operate on HWP
documents directly — packaged as a Claude Code Skill / MCP integration.

> Status: **HWP → HWPX conversion working (1st pass).** Validated on a real
> report against a Hancom-authored HWPX; four hwp2hwpx-chain fidelity defects
> found and fixed (see `docs/findings.md`). Next phase is AI-driven direct HWPX
> editing — see `docs/design.md`.

## How it fits together

```
              ┌─────────────┐   convert    ┌──────────────┐   edit ops    ┌─────────────┐
  source.hwp  │  ConverterBackend          │   .hwpx      │  (python-hwpx) │  edited     │
 (read-only) ─▶  hwp2hwpx (vendored jar) ──▶  (XML/ZIP)   ├──────────────▶│  .hwpx      │
              └─────────────┘   cache       └──────────────┘                └─────────────┘
```

- **The original `.hwp` is the source of truth and is never modified.**
- **The generated `.hwpx` is treated as a regenerable cache artifact** — delete
  it any time and rebuild from the `.hwp`.

## Requirements

- macOS / Linux / **Windows**. The Python CLI is cross-platform; macOS/Linux use
  the bash scripts, Windows uses `scripts\install.ps1` + a prebuilt jar.
- Python ≥ 3.11
- To **build** the converter jar from source: JDK 17+ and Maven
  (`brew install openjdk@17 maven`). To only **run** a prebuilt jar (the Windows
  path): a JRE 17+ suffices.

## Setup

One command builds the converter, installs the `hwp-agent` CLI on your PATH,
and registers the Claude Code skill:

```bash
./scripts/install.sh
```

It's re-runnable. Skip the converter (HWPX-only, no JDK/Maven needed) with
`SKIP_JAR=1 ./scripts/install.sh`. Needs [`uv`](https://docs.astral.sh/uv/) or
`pipx` to put the CLI on PATH. Because the install is *editable*, `convert`
finds the jar automatically — no environment variable to set.

<details><summary>Manual setup (if you'd rather do the steps yourself)</summary>

```bash
./scripts/bootstrap.sh                        # 1. build vendor/hwp2hwpx.jar
uv tool install --editable .                  # 2. CLI on PATH (or: pip install -e ".[dev]")
ln -s "$PWD/skills/hwp-agent" ~/.claude/skills/hwp-agent   # 3. register the skill
```
</details>

`bootstrap.sh` clones [neolord0/hwp2hwpx](https://github.com/neolord0/hwp2hwpx)
(a library with no CLI), builds it together with its dependencies
(`hwplib`, `hwpxlib`) and our thin `scripts/Hwp2HwpxCli.java` entry point, and
fuses them into a single runnable `vendor/hwp2hwpx.jar`. The jar is **not**
committed — it's a reproducible build artifact (see `.gitignore`).

### Windows

**Easiest (no Python, no install):** download the standalone **`hwp-agent.exe`**
from the [windows-exe release](https://github.com/z0nam/hwp-agent/releases/tag/windows-exe)
and run it from a terminal — e.g. `hwp-agent.exe form fill 등록신청서.hwpx --profile`.
The converter jar is bundled inside; install a JRE 17+
([Temurin](https://adoptium.net/), a normal double-click installer) only if you
also need `hwp-agent convert` (.hwp → .hwpx). Form-fill/editing need no Java.

**Developer install (Python):** no JDK/Maven needed — install from GitHub and
fetch the prebuilt converter jar:

```powershell
# one-liner (needs uv or pipx + a JRE 17+ for the converter)
pipx install "git+https://github.com/z0nam/hwp-agent"
hwp-agent setup        # downloads the converter jar to %LOCALAPPDATA%\hwp-agent
```

Or, from a cloned checkout, run the installer (CLI + skill copy + `setup`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

`hwp-agent setup` downloads the jar from the GitHub release and checks for a Java
runtime; install a JRE 17+ (e.g. [Temurin](https://adoptium.net/)) to run
`convert`. HWPX-only use (editing, `form fill`) needs no Java at all.

### Layout engines (for `fit tails`, `pdf`, `docx`, `verify`)

Everything above works without these. They matter only when hwp-agent has to
know **how Hangul lays a document out** — `fit tails` on a document never saved
in Hangul (e.g. a fresh `write` output), and `pdf`/`docx`/`verify`. hwp-agent picks, in order:

1. **hwp2pdf-cli** (`pdf`, `docx`, `verify` only) — Hangul itself on *this* Windows
   PC, through the CLI of the separate [hwp2pdf](https://github.com/z0nam/hwp2pdf)
   project. No server, no token. Found as `$HWP2PDF_CLI`, else `hwp2pdf-cli` on
   `PATH` (the hwp2pdf installer adds it). Needs a logged-in desktop session: run
   over SSH (session 0) Hangul's COM server does not start and the CLI fails after
   about two minutes, so hwp-agent moves on to the next engine.
2. **hwp2pdf server** — Hangul itself, through the conversion server of the separate
   [hwp2pdf](https://github.com/z0nam/hwp2pdf) project (`hwp2pdf serve`, Windows +
   Hancom Office). Exact. DOCX needs it. Hangul may save a long document with only
   its first pages laid out; `fit tails` then judges the remaining paragraphs with
   rhwp and reports the split (e.g. `문단 한글 22 · rhwp 57`). Without rhwp they are
   left unjudged and the result says `일부만 판정` rather than "no targets".
3. **rhwp** — a stand-alone renderer. Runs anywhere, but its line breaks can
   differ from Hangul's; results are labelled `rhwp 조판 기준(한글과 다를 수 있음)`.
4. **none** — `fit tails` prints `판정 엔진 없음` with the reason for each engine
   and exits 3 (`write`/`build --fit-tails` only warn and still write the file).

**hwp2pdf is never used while Hangul is running on that machine** (or the
server has other jobs queued): hwp-agent does not open a second Hangul next to
yours and never closes yours. On your own PC with Hangul open, the local CLI
declines and hwp-agent tries the server, then rhwp. For Hangul-exact results while
you work, point at a server on a machine where nobody keeps Hangul open.

Every `pdf`/`docx`/`verify` result names the engine (`via hwp2pdf-cli · 한컴 · 이 PC`,
`via hwp2pdf · 한컴 · 변환 서버`, `via rhwp · 근사 레이아웃`) and, when a preferred
engine was skipped, a `note:` line saying why. Only the two Hancom engines are
authoritative for pagination and spacing. Force one with `--engine` or
`$HWP_AGENT_RENDER_ENGINE`: `auto` (default, the order above), `hwp2pdf` (Hancom
only: CLI, else server; never rhwp), `local`, `remote`, `rhwp` (that one only).

Setup, by situation:

| You are on | Do this |
|---|---|
| Windows PC with hwp2pdf installed | Nothing for `pdf`/`docx`/`verify`: hwp-agent runs `hwp2pdf-cli` from `PATH` (or `$HWP2PDF_CLI`). For `fit tails` also run `hwp2pdf-cli.exe serve --init` (or its scheduled task); hwp-agent then finds `127.0.0.1:17650` and the token `serve --init` saved. Used only while Hangul is closed. |
| Any PC, shared conversion server | Create `~/.config/hwp-agent/hwp2pdf.json` from [`examples/hwp2pdf.example.json`](examples/hwp2pdf.example.json): `server_url`, and the token in a file named by `token_file` (mode 600) or in `$HWP2PDF_TOKEN`. Never put the token in the JSON or a repo. |
| No server | Install rhwp: download the binary for your OS from [rhwp releases](https://github.com/edwardkim/rhwp/releases) and put it on `PATH`, or set `$RHWP_BIN` to its path. |

Check what you got with `hwp-agent fit tails FILE.hwpx --dry-run`: the report
ends with `[판정: …]` naming the engine, or explains why none was available.
Env overrides: `HWP_AGENT_RENDER_ENGINE`, `HWP2PDF_CLI`, `HWP2PDF_SERVER_URL`,
`HWP2PDF_TOKEN`, `HWP2PDF_TOKEN_FILE`, `HWP2PDF_TIMEOUT`, `HWP2PDF_NO_LOCAL=1`
(skip the `127.0.0.1` probe), `RHWP_BIN`.

## Usage

```bash
hwp-agent convert report.hwp report.hwpx
hwp-agent --version

# render HWP/HWPX -> PDF/DOCX. auto = Hancom via hwp2pdf-cli on this Windows PC,
# else the hwp2pdf conversion server, else local rhwp for PDF. DOCX is Hancom-only
# (rhwp can't). Neither Hancom engine is used while Hangul is running on its
# machine (or the server queue is busy). See "Layout engines".
hwp-agent pdf report.hwpx                    # auto
hwp-agent pdf report.hwpx --engine hwp2pdf   # Hancom only (cli -> server), never rhwp
hwp-agent pdf report.hwpx --engine rhwp      # force rhwp (no Hancom)
hwp-agent docx report.hwpx                   # DOCX -> hwp2pdf-cli, else the server
#   the server needs ~/.config/hwp-agent/hwp2pdf.json (examples/hwp2pdf.example.json):
#   server_url + token via $HWP2PDF_TOKEN or token_file

# fill a Korean form: inspect slots, then fill
hwp-agent form analyze 등록신청서.hwpx --json
hwp-agent form fill 등록신청서.hwpx --set "성명=조남운" -o out.hwpx

# a cell that already holds text is not a "slot" — address it directly
hwp-agent form analyze 자문의견서.hwpx --grid          # every cell + its cell: path
hwp-agent form fill 자문의견서.hwpx --set "cell:0:1:2=□ 적정  ■ 보통  □ 보완" -o out.hwpx

# long content in a page-trapped table (treatAsChar=1) is truncated — let it flow
hwp-agent form fill 자문의견서.hwpx --set "cell:0:1:2=$(cat long.txt)" --allow-table-flow -o out.hwpx

# a sample table with too few rows: delete the sample rows, append blank copies
# of the last row (format kept, text cleared), then --set the new coordinates.
# Order is fixed: --delete-rows (original rows) → --append-rows → --set (final rows)
hwp-agent form fill 신청서.hwpx --delete-rows cell:0:2-13 --append-rows cell:0:+20 \
                    --set "cell:0:2:1=1. 연구 배경" -o out.hwpx

# or copy a whole row group (shaded category row + its detail rows) as a unit:
# rows 2-4 ×7 more → 8 groups; --set then fills cell:0:5:0, cell:0:8:0, …
hwp-agent form fill 신청서.hwpx --repeat-group cell:0:2-4:+7 \
                    --set "cell:0:5:0=Ⅱ. 이론적 검토" -o out.hwpx

# rebalance equal columns by how much content each holds (no text change; total width kept)
hwp-agent form autofit 자문의견서.hwpx --table 0 -o out.hwpx      # add --min-width 0=7000 to pin a column

# auto-fill standing personal data (성명/주소/학력/경력/계좌…) from a saved profile
cp examples/profile.example.json ~/.config/hwp-agent/profile.json   # edit it once
hwp-agent form fill 등록신청서.hwpx --profile --date today -o out.hwpx
```

```bash
# a one-line correction to a document that is already type-set — no re-typeset
hwp-agent text find 최종보고서.hwpx "이러한 단절은"          # check the anchor is unique
hwp-agent text insert 최종보고서.hwpx --before "이러한 단절은" --md 덧붙임.md -o out.hwpx

# anchored on a second-level bullet? say so, or a top-level bullet lands on top of it
hwp-agent text insert 최종보고서.hwpx --after "기능 설명서 방식은" --anchor-level 2 \
                     --md 덧붙임.md -o out.hwpx
```

`text insert` adds paragraphs to a finished document and touches nothing else: each new
paragraph clones the styles of a neighbour at the same indent, existing paragraphs keep
their line-layout cache, and the ZIP container is preserved so Hangul does not flag the
file. Headings and tables are out of scope — they renumber things the rest of the
document already agreed on, so those need a re-typeset with `write`.

`form fill` slot keys can be a label (`성명`), a label path (`성명 > right`),
a stable address (`cell:<table>:<row>:<col>`), a `checkbox:<label>` (`on`/`off`,
□↔■), a `tab:<anchor>` inline field, or a `{{placeholder}}`. Fills overwrite, so
re-running is safe.

`form analyze` reports *slots* — label cells whose neighbour is **empty**. A form
that ships its answer cells pre-filled (repeated `□ 적정 □ 보통 □ 보완` rows, say)
therefore lists few or no slots, even though every one of those cells is
writable. `form analyze --grid` enumerates each cell with the
`cell:<table>:<row>:<col>` key that `form fill` takes, which is the way to reach
them.

Table rows (issue #43): `--delete-rows cell:<t>:<start>-<end>` removes rows and
`--append-rows cell:<t>:+<n>` appends blank copies of the last row, and
`--repeat-group cell:<t>:<start>-<end>:+<n>` inserts n blank copies of rows
start..end right after end (the range is always given explicitly — no group
auto-detection). Appends and repeats are numbered after the deletes; within one call
their ranges must not overlap (run a second `form fill` to nest). Every cell keeps
its border/fill, size, margins and paragraph/character style; only the text is
cleared. `rowAddr`, the table's `rowCnt` and height are rewritten to match, so
`form analyze --grid` shows the new rows. A vertical merge that straddles a range
boundary is refused with an error rather than broken; a merge entirely inside the
range is deleted/copied with it.

Point at a jar elsewhere with `--jar /path/to/hwp2hwpx.jar` or the
`HWP2HWPX_JAR` environment variable (rarely needed — the editable install
locates the bundled jar on its own).

## Use it from an AI chat / a web link (no terminal)

For non-technical users, two zero-terminal surfaces — see **[docs/serving.md](docs/serving.md)**:

- **Web link / ChatGPT** — run `hwp-agent serve` on a machine you control (a Mac
  mini, like a Slack bot); it exposes a drag-and-drop **web upload page** + a REST
  API + `/openapi.json` for a **ChatGPT custom-GPT Action**. Files stay on your
  server (Java conversion runs there). `pip install "hwp-agent[serve]"`.
- **Claude Desktop / Claude Code / Codex** — `hwp-agent mcp` is a local stdio MCP
  server; register it once and just chat ("이 폼을 내 프로필로 채워줘"). It reads
  local files directly, no upload. `pip install "hwp-agent[mcp]"`.

> Web chat can't forward a chat-uploaded file to an external server, so ChatGPT
> users use the web link; the upload-free chat flow is the local-MCP path. Details
> and the exact per-client config are in [docs/serving.md](docs/serving.md).

## Claude plugin

This repository is also a Claude plugin (`.claude-plugin/plugin.json`): the
skill below plus the stdio MCP server, started with
`uvx --python 3.12 --from <plugin dir> --with "mcp>=1.2,<2" hwp-agent mcp` (needs [uv](https://docs.astral.sh/uv/) on PATH).
Python is pinned to 3.12 because uv otherwise picks the newest Python, for which lxml may have
no binary wheel yet (then the MCP server cannot start without a C toolchain; #75).
Jeju Research Institute staff get it through the JAIX marketplace
([jeju-inst/jaix-kit](https://github.com/jeju-inst/jaix-kit)), pinned to a tested release:

```
/plugin marketplace add jeju-inst/jaix-kit
/plugin install hwp-agent@jaix
```

## Claude Code Skill

`skills/hwp-agent/` packages the authoring workflow as a [Claude Code
Skill](https://docs.claude.com/en/docs/claude-code/skills) — it teaches Claude
the inspect-first loop (`classify` → `styles` → `instructions` → `write`/`form
fill` → verify), the template token conventions (`{{body}}`, `{{appendix}}`,
`{{table_template}}`, `{{chapter_number=N}}`), and the Markdown→HWPX rules.

`./scripts/install.sh` registers it globally at `~/.claude/skills/hwp-agent`.
For a single project instead, symlink it there:
`ln -s "$PWD/skills/hwp-agent" .claude/skills/hwp-agent`.

Then Claude Code invokes it automatically for HWP/HWPX tasks, or on demand with
`/hwp-agent`. `SKILL.md` keeps the common rules and a "Which file to read" table;
per-workflow detail (`write` Markdown, footnotes·links, equations, figures, cover
metadata, the command table, `extract`, hand-editing) lives in `references/*.md`
and is read only for that step, so one invocation stays within the 10k-token
budget. `references/template-convention.md` and `references/tables.md` are
snapshots of `docs/`; refresh them with
`cp docs/{template-convention,tables}.md skills/hwp-agent/references/`.

## Development

```bash
ruff check .
pytest            # smoke tests; the end-to-end test self-skips without a built jar + sample
```

Drop a sample document at `tests/fixtures/sample.hwp` to exercise the
end-to-end conversion test. **`tests/fixtures/` is git-ignored** — never commit
real institutional documents.

## Licensing

`hwp-agent` is **Apache-2.0** (see [`LICENSE`](LICENSE)). It builds on, and is
compatible with, its Apache-2.0 dependencies: `python-hwpx`, `hwp2hwpx`,
`hwplib`, `hwpxlib`.
