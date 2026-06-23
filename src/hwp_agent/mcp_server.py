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


def _build():
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise SystemExit(
            'the MCP server needs the "mcp" package. Install it with:\n'
            '  pip install "hwp-agent[mcp]"   (or: uv pip install "mcp>=1.2")'
        ) from exc

    from .ops import analyze_form, extract_markdown, fill_form, fill_from_profile

    mcp = FastMCP("hwp-agent")

    @mcp.tool()
    def analyze_form_slots(path: str) -> str:
        """List the fillable slots in an HWPX form (labels, checkboxes, cell paths).

        Args: path — absolute path to a local .hwpx file.
        Returns: JSON {"slots": [...]} describing every fillable position.
        """
        return json.dumps(analyze_form(path).as_dict(), ensure_ascii=False)

    @mcp.tool()
    def fill_form_slots(path: str, mapping_json: str, output: str) -> str:
        """Fill specific slots in an HWPX form and save to `output`.

        Args:
          path — local .hwpx form.
          mapping_json — JSON object of {slot_key: value}. Keys may be a label
            ("성명"), a label path ("성명 > right"), "cell:<t>:<r>:<c>",
            "checkbox:<label>" (value "on"/"off"), "tab:<anchor>", or a
            "{{placeholder}}". Fills overwrite.
          output — local path to write the filled .hwpx.
        Returns: JSON {"filled": [...], "missing": [...]}.
        """
        mapping = json.loads(mapping_json)
        res = fill_form(path, mapping, output=output)
        return json.dumps(res.as_dict(), ensure_ascii=False)

    @mcp.tool()
    def fill_form_from_profile(
        path: str, output: str, profile_path: str | None = None, date: str | None = None
    ) -> str:
        """Auto-fill an HWPX form from a saved personal-data profile.

        Args:
          path — local .hwpx form.
          output — local path to write the filled .hwpx.
          profile_path — profile JSON path (default: ~/.config/hwp-agent/profile.json).
          date — pass "today" to fill date/작성일 slots with today's date.
        Returns: JSON {"filled": [{slot, field, value}], "blank": [labels]}.
        """
        res = fill_from_profile(path, profile_path, output=output, date=date)
        return json.dumps(res.as_dict(), ensure_ascii=False)

    @mcp.tool()
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

    @mcp.tool()
    def normalize_template(path: str, output: str, dry_run: bool = False) -> str:
        """Declare AI:HEADING_n/AI:BULLET_n styles on a flat HWPX template.

        Detects the numbering ladder of a flat (non-outline) template and writes
        the AI: role declarations into a container-preserving copy, so classify
        reports structured and authoring (write) can target its styles.

        Args:
          path — local flat .hwpx template (never modified).
          output — local path for the normalized copy.
          dry_run — plan only; write nothing.
        Returns: JSON report (actions with rationale, skipped styles, warnings).
        """
        from .ops import apply_normalization, plan_normalization

        plan = plan_normalization(path)
        report = plan.as_dict()
        report["applied"] = bool(plan.actions) and not dry_run
        report["output"] = output if report["applied"] else None
        if report["applied"]:
            apply_normalization(path, plan, output)
        return json.dumps(report, ensure_ascii=False)

    @mcp.tool()
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
