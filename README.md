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

No JDK/Maven needed — install from GitHub and fetch the prebuilt converter jar:

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

## Usage

```bash
hwp-agent convert report.hwp report.hwpx
hwp-agent --version

# fill a Korean form: inspect slots, then fill
hwp-agent form analyze 등록신청서.hwpx --json
hwp-agent form fill 등록신청서.hwpx --set "성명=조남운" -o out.hwpx

# auto-fill standing personal data (성명/주소/학력/경력/계좌…) from a saved profile
cp examples/profile.example.json ~/.config/hwp-agent/profile.json   # edit it once
hwp-agent form fill 등록신청서.hwpx --profile --date today -o out.hwpx
```

`form fill` slot keys can be a label (`성명`), a label path (`성명 > right`),
a stable address (`cell:<table>:<row>:<col>`), a `checkbox:<label>` (`on`/`off`,
□↔■), a `tab:<anchor>` inline field, or a `{{placeholder}}`. Fills overwrite, so
re-running is safe.

Point at a jar elsewhere with `--jar /path/to/hwp2hwpx.jar` or the
`HWP2HWPX_JAR` environment variable (rarely needed — the editable install
locates the bundled jar on its own).

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
`/hwp-agent`. The skill's `references/` are snapshots of `docs/`; refresh them
with `cp docs/{template-convention,tables}.md skills/hwp-agent/references/`.

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
