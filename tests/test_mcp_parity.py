"""The MCP form tools must offer what `hwp-agent form …` offers.

Every CLI option of `form analyze/fill/autofit` is either a parameter of the
matching MCP tool (same name, snake_case) or listed below with the reason it has
no MCP counterpart. A new CLI flag without an MCP parameter fails here, so the
two surfaces can't silently drift apart again.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import pytest

from hwp_agent import mcp_server
from hwp_agent.cli.main import build_parser

# CLI dest -> why the MCP tool has no parameter of that name
CLI_ONLY = {
    "analyze": {
        "action": "subcommand selector",
        "file": "MCP takes `path`",
        "json": "MCP always returns JSON",
    },
    "fill": {
        "action": "subcommand selector",
        "file": "MCP takes `path`",
        "map": "MCP takes the mapping inline as `mapping_json`",
        "set": "MCP takes the mapping inline as `mapping_json`",
        "profile": "separate tool: fill_form_from_profile",
        "date": "separate tool: fill_form_from_profile",
        "json": "MCP always returns JSON",
    },
    "autofit": {
        "action": "subcommand selector",
        "file": "MCP takes `path`",
        "json": "MCP always returns JSON",
    },
}
TOOLS = {
    "analyze": mcp_server.analyze_form_slots,
    "fill": mcp_server.fill_form_slots,
    "autofit": mcp_server.autofit_table_columns,
}


def _form_subparser(name: str) -> argparse.ArgumentParser:
    def sub(p: argparse.ArgumentParser, key: str) -> argparse.ArgumentParser:
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction) and key in a.choices:
                return a.choices[key]
        raise KeyError(key)

    return sub(sub(build_parser(), "form"), name)


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_every_cli_form_option_has_an_mcp_parameter(name: str) -> None:
    dests = {
        a.dest
        for a in _form_subparser(name)._actions
        if not isinstance(a, argparse._HelpAction)
    }
    params = set(inspect.signature(TOOLS[name]).parameters)
    missing = sorted(dests - params - set(CLI_ONLY[name]))
    assert not missing, f"`form {name}` options missing from MCP tool: {missing}"


def test_profile_tool_covers_profile_options() -> None:
    params = set(inspect.signature(mcp_server.fill_form_from_profile).parameters)
    assert {"profile_path", "date", "output"} <= params


# --- behaviour through the MCP bodies -------------------------------------- #
@pytest.fixture()
def form(tmp_path: Path) -> Path:
    from test_form_rows import _make_form

    return _make_form(tmp_path / "form.hwpx")


def test_mcp_fill_restructures_rows_then_fills(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    res = json.loads(
        mcp_server.fill_form_slots(
            str(form), json.dumps({"cell:0:5:0": "Ⅱ. 복제"}), str(out),
            repeat_group=["cell:0:2-4:+1"], append_rows=["cell:0:+1"],
            delete_rows=["cell:0:5"],
        )
    )
    assert res["missing"] == [] and len(res["rows"]) == 3
    grid = json.loads(mcp_server.analyze_form_slots(str(out), grid=True))["cells"]
    firsts = [c["text"] for c in grid if c["col"] == 0]
    assert firsts == ["구분", "구분", "Ⅰ. 대분류", "소", "소", "Ⅱ. 복제", "", "", "끝", ""]


def test_mcp_fill_reports_row_errors(form: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.hwpx"
    res = json.loads(
        mcp_server.fill_form_slots(str(form), "{}", str(out), delete_rows=["cell:0:3"])
    )
    assert "crosses the range boundary" in res["error"]
    assert not out.exists()


def test_mcp_autofit(form: Path, tmp_path: Path) -> None:
    res = json.loads(
        mcp_server.autofit_table_columns(str(form), str(tmp_path / "a.hwpx"), min_width=["0=3000"])
    )
    assert res["ncols"] == 3 and sum(res["new_widths"]) == res["total"]
    bad = json.loads(mcp_server.autofit_table_columns(str(form), str(tmp_path / "b.hwpx"),
                                                      min_width=["x"]))
    assert "error" in bad
