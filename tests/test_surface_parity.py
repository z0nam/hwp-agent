"""The MCP tools and REST endpoints must offer what `hwp-agent form …` offers.

Every CLI option of `form analyze/fill/autofit` is either a parameter of the
matching MCP tool / a field of the matching REST endpoint (same name, snake_case)
or listed below with the reason it has no counterpart. A new CLI flag without an
MCP parameter or REST field fails here, so the surfaces can't silently drift apart.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import pytest

from hwp_agent import mcp_server
from hwp_agent.cli.main import build_parser

# CLI dest -> why the MCP tool / REST endpoint has no parameter of that name
CLI_ONLY = {
    "analyze": {
        "action": "subcommand selector",
        "file": "MCP takes `path`",
        "json": "MCP always returns JSON",
    },
    "fill": {
        "action": "subcommand selector",
        "file": "MCP takes `path`; REST takes the upload as `file`",
        "map": "MCP takes the mapping inline as `mapping_json`",
        "set": "MCP takes the mapping inline as `mapping_json`",
        "profile": "separate tool/endpoint: fill_form_from_profile, /api/fill-profile",
        "date": "separate tool/endpoint: fill_form_from_profile, /api/fill-profile",
        "json": "MCP always returns JSON; REST reports in the X-HWP-Agent-Report header",
        "output": "REST returns the file as the response body",
    },
    "autofit": {
        "action": "subcommand selector",
        "file": "MCP takes `path`",
        "json": "MCP always returns JSON; REST reports in the X-HWP-Agent-Report header",
        "output": "REST returns the file as the response body",
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


ENDPOINTS = {"analyze": "/api/analyze", "fill": "/api/fill", "autofit": "/api/autofit"}


def _cli_dests(name: str) -> set[str]:
    return {
        a.dest
        for a in _form_subparser(name)._actions
        if not isinstance(a, argparse._HelpAction)
    }


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_every_cli_form_option_has_an_mcp_parameter(name: str) -> None:
    params = set(inspect.signature(TOOLS[name]).parameters)
    # MCP-side names for CLI-side ones it renames
    missing = sorted(_cli_dests(name) - params - set(CLI_ONLY[name]) - {"output"})
    assert not missing, f"`form {name}` options missing from MCP tool: {missing}"
    if name != "analyze":
        assert "output" in params


@pytest.mark.parametrize("name", sorted(ENDPOINTS))
def test_every_cli_form_option_has_a_rest_field(name: str) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from hwp_agent.server.app import create_app

    route = next(r for r in create_app().routes if getattr(r, "path", "") == ENDPOINTS[name])
    fields = {p.name for p in route.dependant.body_params}
    missing = sorted(_cli_dests(name) - fields - set(CLI_ONLY[name]))
    assert not missing, f"`form {name}` options missing from {ENDPOINTS[name]}: {missing}"


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


# --- behaviour through REST ------------------------------------------------- #
@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from hwp_agent.server.app import create_app

    return TestClient(create_app())


def _upload(path: Path):
    return {"file": ("form.hwpx", path.read_bytes(), "application/octet-stream")}


def test_rest_fill_restructures_rows_then_fills(client, form: Path, tmp_path: Path) -> None:
    r = client.post(
        "/api/fill",
        files=_upload(form),
        data={
            "mapping_json": json.dumps({"cell:0:5:0": "Ⅱ. 복제"}),
            "repeat_group": ["cell:0:2-4:+1"],
            "append_rows": ["cell:0:+1"],
            "delete_rows": ["cell:0:5"],
        },
    )
    assert r.status_code == 200, r.text
    report = json.loads(r.headers["X-HWP-Agent-Report"])
    assert report["missing"] == [] and len(report["rows"]) == 3
    out = tmp_path / "out.hwpx"
    out.write_bytes(r.content)
    g = client.post("/api/analyze", files=_upload(out), data={"grid": "true"}).json()["cells"]
    firsts = [c["text"] for c in g if c["col"] == 0]
    assert firsts == ["구분", "구분", "Ⅰ. 대분류", "소", "소", "Ⅱ. 복제", "", "", "끝", ""]


def test_rest_fill_row_error_is_422(client, form: Path) -> None:
    r = client.post("/api/fill", files=_upload(form), data={"delete_rows": ["cell:0:3"]})
    assert r.status_code == 422
    assert "crosses the range boundary" in r.json()["detail"]
    bad = client.post("/api/fill", files=_upload(form), data={"mapping_json": "[1]"})
    assert bad.status_code == 422


def test_rest_autofit(client, form: Path) -> None:
    r = client.post("/api/autofit", files=_upload(form), data={"min_width": ["0=3000"]})
    assert r.status_code == 200, r.text
    summary = json.loads(r.headers["X-HWP-Agent-Report"])
    assert summary["ncols"] == 3 and sum(summary["new_widths"]) == summary["total"]
    assert client.post(
        "/api/autofit", files=_upload(form), data={"min_width": ["x"]}
    ).status_code == 422
