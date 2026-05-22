# hwp-agent

Edit **HWP / HWPX** — the document standard used across Korean public and
research institutions — directly with AI, **without a lossy DOCX round-trip**.

Going through DOCX (the common "just convert it" shortcut) silently drops or
mangles Korean-specific formatting: cover-page layouts, 표(table) styling,
머리말/꼬리말, numbering, fonts. `hwp-agent` works in the native format instead,
so formatting is preserved. The end goal is to let an AI operate on HWP
documents directly — packaged as a Claude Code Skill / MCP integration.

> Status: **early scaffold.** Today it does HWP → HWPX conversion. Editing
> operations and verification land in later slices (see `docs/design.md`).

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

- macOS / Linux (developed on Apple Silicon)
- Python ≥ 3.11
- JDK 17+ and Maven — used only to *build* the converter jar
  - `brew install openjdk@17 maven`

## Setup

```bash
# 1. Build the HWP→HWPX converter into vendor/hwp2hwpx.jar
./scripts/bootstrap.sh

# 2. Install the Python package (editable)
uv pip install -e ".[dev]"     # or:  pip install -e ".[dev]"
```

`bootstrap.sh` clones [neolord0/hwp2hwpx](https://github.com/neolord0/hwp2hwpx)
(a library with no CLI), builds it together with its dependencies
(`hwplib`, `hwpxlib`) and our thin `scripts/Hwp2HwpxCli.java` entry point, and
fuses them into a single runnable `vendor/hwp2hwpx.jar`. The jar is **not**
committed — it's a reproducible build artifact (see `.gitignore`).

## Usage

```bash
hwp-agent convert report.hwp report.hwpx
hwp-agent --version
```

Point at a jar elsewhere with `--jar /path/to/hwp2hwpx.jar` or the
`HWP2HWPX_JAR` environment variable.

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
