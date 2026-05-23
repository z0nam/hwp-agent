# Design

## Goal

Let an AI edit HWP/HWPX documents in their native format, so Korean-specific
formatting survives. DOCX conversion is explicitly rejected as the editing
substrate because it loses too much.

## Status

- **Phase 1 — HWP → HWPX conversion: complete (1st pass).** Validated on a real
  2.7 MB report against a Hancom-authored HWPX of the same document. Four
  hwp2hwpx-chain fidelity defects were found and fixed (3 via post-conversion
  normalization, 1 via an overlaid hwplib patch — also submitted upstream as
  neolord0/hwplib#306). Text, images, tables, layout, and special characters
  now round-trip with high fidelity. See `docs/findings.md`.
- **Phase 2 — AI-driven direct HWPX editing: next.** This is the project's real
  goal; conversion was the enabling groundwork.

## Pipeline

```
.hwp  ──convert──▶  .hwpx  ──ops──▶  .hwpx'  ──verify──▶  ok / diff
(truth)            (cache)          (edited)
```

1. **convert** (`hwp_agent.convert`) — HWP → HWPX. ✅ Done. Backend-abstracted;
   the default backend shells out to the vendored `hwp2hwpx` jar (with our
   overlaid hwplib patch) and repairs known output defects in `_normalize_hwpx`.
   Other backends (e.g. hwpilot) can be dropped in behind `ConverterBackend`.
2. **ops** (`hwp_agent.ops`) — structured edits on the HWPX. ◀ **active phase:
   AI-driven direct HWPX editing.** First op shipped: **document metadata**
   (`ops.metadata` — read/fill title, creator, keyword, … in `content.hpf`'s
   `<opf:metadata>`, via the HwpxPackage API; CLI `hwp-agent meta`). Next:
   cover-page text/table fill (using python-hwpx `find_cell_by_label` /
   `fill_by_path` / `replace_text_in_runs`).
3. **verify** (`hwp_agent.verify`) — round-trip and structural checks that an
   edit didn't corrupt the package. *Later slice.*

## Key decisions

- **HWP is read-only truth; HWPX is a cache.** Conversion output can always be
  regenerated and is git-ignored. This keeps original institutional documents
  pristine.
- **Converter is abstracted** behind `ConverterBackend.convert()` so we are not
  married to hwp2hwpx.
- **hwp2hwpx is a library, not a CLI.** We add a one-file Java wrapper
  (`scripts/Hwp2HwpxCli.java`) and bundle a fat jar via `scripts/bootstrap.sh`,
  rather than reaching into the JVM from Python (jpype/py4j) — simplest, fewest
  moving parts. The jpype route remains a documented fallback if subprocess
  startup cost ever matters.
- **Build artifacts are not committed** (`vendor/*.jar`): reproducible builds
  over vendored binaries.

## Open questions / later

- Editing API surface: how `ops` should express edits (templated fields vs.
  free-form structural edits).
- Verification depth: schema validation vs. semantic round-trip diff.
- Packaging as a Claude Code Skill / MCP server.
