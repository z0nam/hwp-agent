"""Local stdio MCP server (`hwp-agent mcp`).

Exposes the hwp-agent ops as MCP tools so a local AI client — Claude Desktop,
Claude Code, or Codex — can drive them over stdio. Because the client spawns this
process on the user's own machine, the tools operate on **local file paths** and
nothing leaves the box.

Add it to a client with (all point at the same command):

    # Claude Code
    claude mcp add --transport stdio hwp-agent -- hwp-agent mcp
    # Claude Desktop  (claude_desktop_config.json)
    {"mcpServers": {"hwp-agent": {"command": "hwp-agent", "args": ["mcp"]}}}
    # Codex  (~/.codex/config.toml)
    [mcp_servers.hwp-agent]
    command = "hwp-agent"
    args = ["mcp"]

stdio rule: never write to stdout (it carries JSON-RPC); logs go to stderr.
"""

from __future__ import annotations

import json
import sys


# --------------------------------------------------------------------------- #
# Tool bodies — plain functions so they are testable without the `mcp` package.
# The form tools mirror `hwp-agent form …` option-for-option (parameter names are
# the CLI flags in snake_case); tests/test_surface_parity.py keeps the two in step.
# --------------------------------------------------------------------------- #
def analyze_form_slots(path: str, grid: bool = False) -> str:
    """List the fillable slots in an HWPX form (labels, checkboxes, cell paths).

    Args:
      path — local .hwpx.
      grid — list every table cell with its "cell:<t>:<r>:<c>" path and text
        (when the target cell already holds text, or after a row restructure).
    Returns: JSON {"slots": [...]}, or {"cells": [...]} with grid=true.
    """
    from .ops import analyze_form, dump_grid

    if grid:
        return json.dumps(
            {"cells": [c.as_dict() for c in dump_grid(path)]}, ensure_ascii=False
        )
    return json.dumps(analyze_form(path).as_dict(), ensure_ascii=False)


def fill_form_slots(
    path: str,
    mapping_json: str,
    output: str,
    delete_rows: list[str] | None = None,
    append_rows: list[str] | None = None,
    repeat_group: list[str] | None = None,
    allow_table_flow: bool = False,
    precise: bool = True,
) -> str:
    """Fill slots in an HWPX form and save to `output`.

    Args:
      path — local .hwpx form; output — path for the filled .hwpx.
      mapping_json — JSON {slot_key: value} ("{}" = only restructure rows). Keys:
        label ("성명"), label path ("성명 > right"), "cell:<t>:<r>:<c>",
        "checkbox:<label>" ("on"/"off"), "tab:<anchor>", "{{placeholder}}".
      delete_rows — ["cell:<t>:<start>-<end>"] (0-based, inclusive, original rows).
      append_rows — ["cell:<t>:+<n>"]: n blank copies of the last row.
      repeat_group — ["cell:<t>:<start>-<end>:+<n>"]: n blank copies of rows
        start..end after end. Runs delete → append/repeat → fill, so "cell:" keys
        address the new rows; a merge across a range boundary is refused.
      allow_table_flow — let long cells flow across pages (changes table layout).
      precise — precise label detection (default); false = every empty-neighbour cell.
    Returns: JSON {"rows", "filled", "missing", "warnings"} or {"error"}.
    """
    from .ops import RowOpError, fill_form, parse_row_ops

    mapping = json.loads(mapping_json or "{}")
    try:
        row_ops = parse_row_ops(delete_rows, append_rows, repeat_group)
        res = fill_form(
            path, mapping, output=output, precise=precise,
            allow_table_flow=allow_table_flow, row_ops=row_ops,
        )
    except RowOpError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(res.as_dict(), ensure_ascii=False)


def autofit_table_columns(
    path: str, output: str, table: int = 0, min_width: list[str] | None = None
) -> str:
    """Rebalance a table's column widths by content volume (total width kept, no text change).

    Args:
      path — local .hwpx; output — path for the result.
      table — table index in document order (as in "cell:<t>:…").
      min_width — ["COL=N"]: minimum column width in HWPUNIT.
    Returns: JSON {"table_index", "ncols", "old_widths", "new_widths", "total"} or {"error"}.
    """
    from .ops import autofit_table, parse_min_widths

    try:
        mins = parse_min_widths(min_width)
        summary = autofit_table(path, table, min_widths=mins or None, output=output)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(summary, ensure_ascii=False)


def fill_form_from_profile(
    path: str, output: str, profile_path: str | None = None, date: str | None = None
) -> str:
    """Auto-fill an HWPX form from a saved personal-data profile.

    Args:
      path — local .hwpx form; output — path for the filled .hwpx.
      profile_path — default ~/.config/hwp-agent/profile.json.
      date — "today" fills date/작성일 slots.
    Returns: JSON {"filled": [{slot, field, value}], "blank": [labels]}.
    """
    from .ops import fill_from_profile

    res = fill_from_profile(path, profile_path, output=output, date=date)
    return json.dumps(res.as_dict(), ensure_ascii=False)


def _build():
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise SystemExit(
            'the MCP server needs the "mcp" package. Install it with:\n'
            '  pip install "hwp-agent[mcp]"   (or: uv pip install "mcp>=1.2,<2")'
        ) from exc

    from .ops import extract_markdown

    mcp = FastMCP("hwp-agent")

    def register(fn):
        # Every tool returns a plain string, so the auto-generated outputSchema
        # ({"result": string}) only adds always-loaded tokens. mcp<1.10 has no
        # structured_output and never emitted one.
        try:
            return mcp.tool(structured_output=False)(fn)
        except TypeError:
            return mcp.tool()(fn)

    for tool in (
        analyze_form_slots,
        fill_form_slots,
        autofit_table_columns,
        fill_form_from_profile,
    ):
        register(tool)

    @register
    def convert_hwp_to_hwpx(src: str, dst: str) -> str:
        """Convert a local .hwp to .hwpx (needs Java + the converter jar).

        Args: src — local .hwp; dst — output .hwpx path.
        Returns: a status string. Run `hwp-agent setup` first if the jar is missing.
        """
        from .convert import Hwp2HwpxBackend

        backend = Hwp2HwpxBackend()
        if not backend.is_available():
            return "error: converter unavailable — install a JRE 17+ and run `hwp-agent setup`."
        result = backend.convert(src, dst)
        if result.returncode != 0:
            return f"error: conversion failed (exit {result.returncode}): {result.stderr[:300]}"
        return f"ok: wrote {dst}"

    @register
    def normalize_template(path: str, output: str, dry_run: bool = False) -> str:
        """Declare AI:HEADING_n/AI:BULLET_n styles on a flat HWPX template (for `write`).

        Args:
          path — local flat .hwpx (never modified); output — path for the copy.
          dry_run — plan only.
        Returns: JSON report (actions, skipped styles, warnings).
        """
        from .ops import apply_normalization, plan_normalization

        plan = plan_normalization(path)
        report = plan.as_dict()
        report["applied"] = bool(plan.actions) and not dry_run
        report["output"] = output if report["applied"] else None
        if report["applied"]:
            apply_normalization(path, plan, output)
        return json.dumps(report, ensure_ascii=False)

    @register
    def extract_to_markdown(path: str, body_only: bool = False) -> str:
        """Extract an HWPX document's text as Markdown (headings, tables, bullets).

        Args: path — local .hwpx; body_only — skip cover/TOC before the first H1.
        Returns: the Markdown text.
        """
        return extract_markdown(path, body_only=body_only)

    return mcp


def main() -> int:
    print("hwp-agent MCP server (stdio) ready", file=sys.stderr)
    _build().run(transport="stdio")
    return 0
