"""Tests for ``hwp-agent setup --register / --unregister`` (issue #29)."""

from __future__ import annotations

import datetime
import json
import tomllib
from pathlib import Path

import pytest

from hwp_agent.cli.main import main as cli_main
from hwp_agent.ops.setup_mcp import (
    CLIENT_ORDER,
    _write_text_atomic,
    config_path_for,
    detect_installed_clients,
    format_registration_report,
    is_client_installed,
    register_clients,
    resolve_targets,
    unregister_clients,
)


def test_resolve_targets_aliases_and_dedup() -> None:
    # `claude,codex,gemini` expands `claude` into both Desktop and Code.
    assert resolve_targets("claude,codex,gemini") == (
        "claude-desktop",
        "claude-code",
        "codex",
        "gemini",
    )
    assert resolve_targets(["gemini-cli", "desktop"]) == ("claude-desktop", "gemini")
    assert resolve_targets("all") == CLIENT_ORDER


def test_resolve_targets_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="알 수 없는 클라이언트"):
        resolve_targets("claude,unknown-ai")


def test_config_paths_macos_and_windows(tmp_path: Path) -> None:
    mac_cfg = config_path_for(
        "claude-desktop", home=tmp_path, env={}, platform="darwin"
    )
    assert mac_cfg == (
        tmp_path
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )

    appdata = tmp_path / "Roaming"
    win_cfg = config_path_for(
        "claude-desktop",
        home=tmp_path,
        env={"APPDATA": str(appdata)},
        platform="win32",
    )
    assert win_cfg == appdata / "Claude" / "claude_desktop_config.json"

    assert (
        config_path_for("claude-code", home=tmp_path, env={}, platform="darwin")
        == tmp_path / ".claude.json"
    )
    assert (
        config_path_for("codex", home=tmp_path, env={}, platform="darwin")
        == tmp_path / ".codex" / "config.toml"
    )
    assert (
        config_path_for("gemini", home=tmp_path, env={}, platform="darwin")
        == tmp_path / ".gemini" / "settings.json"
    )


def test_register_from_empty_and_idempotent_repeat(tmp_path: Path) -> None:
    cmd = "/opt/bin/hwp-agent"
    actions1 = register_clients(
        CLIENT_ORDER, command=cmd, home=tmp_path, env={}, platform="darwin"
    )
    assert [a.status for a in actions1] == ["added", "added", "added", "added"]

    # Verify JSON configs
    desktop_cfg = config_path_for(
        "claude-desktop", home=tmp_path, env={}, platform="darwin"
    )
    assert json.loads(desktop_cfg.read_text(encoding="utf-8")) == {
        "mcpServers": {"hwp-agent": {"command": cmd, "args": ["mcp"]}}
    }

    code_cfg = config_path_for("claude-code", home=tmp_path, env={}, platform="darwin")
    assert json.loads(code_cfg.read_text(encoding="utf-8")) == {
        "mcpServers": {
            "hwp-agent": {"type": "stdio", "command": cmd, "args": ["mcp"]}
        }
    }

    gemini_cfg = config_path_for("gemini", home=tmp_path, env={}, platform="darwin")
    assert json.loads(gemini_cfg.read_text(encoding="utf-8")) == {
        "mcpServers": {"hwp-agent": {"command": cmd, "args": ["mcp"]}}
    }

    # Verify Codex TOML config
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    assert tomllib.loads(codex_cfg.read_text(encoding="utf-8")) == {
        "mcp_servers": {"hwp-agent": {"command": cmd, "args": ["mcp"]}}
    }

    # Second run with identical command is a no-op ("unchanged")
    actions2 = register_clients(
        CLIENT_ORDER, command=cmd, home=tmp_path, env={}, platform="darwin"
    )
    assert [a.status for a in actions2] == [
        "unchanged",
        "unchanged",
        "unchanged",
        "unchanged",
    ]

    # Third run with a new command path updates in place ("updated")
    new_cmd = r"C:\Users\researcher\AppData\Local\hwp-agent\hwp-agent.exe"
    actions3 = register_clients(
        CLIENT_ORDER, command=new_cmd, home=tmp_path, env={}, platform="darwin"
    )
    assert [a.status for a in actions3] == ["updated", "updated", "updated", "updated"]
    assert (
        tomllib.loads(codex_cfg.read_text(encoding="utf-8"))["mcp_servers"][
            "hwp-agent"
        ]["command"]
        == new_cmd
    )


def test_register_preserves_other_servers_and_settings(tmp_path: Path) -> None:
    # Seed each config with unrelated settings + another MCP server
    desktop_cfg = config_path_for(
        "claude-desktop", home=tmp_path, env={}, platform="darwin"
    )
    desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
    desktop_cfg.write_text(
        json.dumps(
            {
                "preferences": {"theme": "dark"},
                "mcpServers": {
                    "other-tool": {"command": "/usr/local/bin/other", "args": ["serve"]}
                },
            }
        ),
        encoding="utf-8",
    )

    code_cfg = config_path_for("claude-code", home=tmp_path, env={}, platform="darwin")
    code_cfg.write_text(
        json.dumps(
            {
                "numStartups": 42,
                "mcpServers": {
                    "dayone": {
                        "type": "stdio",
                        "command": "/usr/local/bin/dayone",
                        "args": ["mcp"],
                    },
                    "hwp-agent": {
                        "type": "stdio",
                        "command": "/old/path/hwp-agent",
                        "args": ["mcp"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    codex_cfg.write_text(
        '# user comment preserved\nmodel = "gpt-5"\n\n'
        "[mcp_servers.korea_rio]\n"
        'command = "/opt/korea-rio"\n'
        "startup_timeout_sec = 60\n\n"
        "[mcp_servers.hwp-agent]\n"
        'command = "/old/hwp-agent"\n'
        'args = ["mcp"]\n\n'
        "[mcp_servers.spacemath]\n"
        'command = "/opt/spacemath"\n',
        encoding="utf-8",
    )

    gemini_cfg = config_path_for("gemini", home=tmp_path, env={}, platform="darwin")
    gemini_cfg.parent.mkdir(parents=True, exist_ok=True)
    gemini_cfg.write_text(
        json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}}),
        encoding="utf-8",
    )

    cmd = "/new/bin/hwp-agent"
    actions = register_clients(
        CLIENT_ORDER, command=cmd, home=tmp_path, env={}, platform="darwin"
    )
    status_by_client = {a.client: a.status for a in actions}
    assert status_by_client == {
        "claude-desktop": "added",
        "claude-code": "updated",
        "codex": "updated",
        "gemini": "added",
    }

    # Check preservation
    d_data = json.loads(desktop_cfg.read_text(encoding="utf-8"))
    assert d_data["preferences"] == {"theme": "dark"}
    assert d_data["mcpServers"]["other-tool"] == {
        "command": "/usr/local/bin/other",
        "args": ["serve"],
    }
    assert d_data["mcpServers"]["hwp-agent"] == {"command": cmd, "args": ["mcp"]}

    c_data = json.loads(code_cfg.read_text(encoding="utf-8"))
    assert c_data["numStartups"] == 42
    assert "dayone" in c_data["mcpServers"]
    assert c_data["mcpServers"]["hwp-agent"]["command"] == cmd

    codex_raw = codex_cfg.read_text(encoding="utf-8")
    assert "# user comment preserved" in codex_raw
    codex_data = tomllib.loads(codex_raw)
    assert codex_data["model"] == "gpt-5"
    assert codex_data["mcp_servers"]["korea_rio"]["command"] == "/opt/korea-rio"
    assert codex_data["mcp_servers"]["spacemath"]["command"] == "/opt/spacemath"
    assert codex_data["mcp_servers"]["hwp-agent"] == {"command": cmd, "args": ["mcp"]}

    g_data = json.loads(gemini_cfg.read_text(encoding="utf-8"))
    assert g_data["security"] == {"auth": {"selectedType": "gemini-api-key"}}
    assert g_data["mcpServers"]["hwp-agent"] == {"command": cmd, "args": ["mcp"]}

    # Unregister removes ONLY hwp-agent and preserves everything else
    unreg = unregister_clients(
        CLIENT_ORDER, home=tmp_path, env={}, platform="darwin"
    )
    assert [u.status for u in unreg] == ["removed", "removed", "removed", "removed"]

    assert "hwp-agent" not in json.loads(desktop_cfg.read_text(encoding="utf-8"))["mcpServers"]
    assert "other-tool" in json.loads(desktop_cfg.read_text(encoding="utf-8"))["mcpServers"]

    codex_after = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert "hwp-agent" not in codex_after["mcp_servers"]
    assert "korea_rio" in codex_after["mcp_servers"]
    assert "spacemath" in codex_after["mcp_servers"]

    # Second unregister is a no-op
    unreg2 = unregister_clients(
        CLIENT_ORDER, home=tmp_path, env={}, platform="darwin"
    )
    assert [u.status for u in unreg2] == ["noop", "noop", "noop", "noop"]


def test_detect_and_guide_output_only_includes_selected_clients(tmp_path: Path) -> None:
    (tmp_path / ".codex").mkdir()
    detected = detect_installed_clients(
        home=tmp_path, env={}, platform="linux", which_fn=lambda _name: None
    )
    assert detected == ("codex",)

    actions = register_clients(
        ("claude-desktop", "codex"),
        command="/usr/local/bin/hwp-agent",
        home=tmp_path,
        env={},
        platform="linux",
    )
    report = format_registration_report(actions)
    assert "Claude Desktop" in report
    assert "Codex" in report
    assert "Gemini CLI" not in report
    assert "Claude Code" not in report
    assert "https://hwp.namun.net" in report


def test_cli_setup_register_and_guide_out_utf8_bom(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / ".gemini"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    guide_file = tmp_path / "guide.txt"
    rc = cli_main(
        [
            "setup",
            "--register",
            "claude-code,codex,gemini",
            "--command",
            "/custom/hwp-agent",
            "--guide-out",
            str(guide_file),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "[MCP 등록 결과]" in out
    assert "[이렇게 쓰세요]" in out

    # Guide file starts with UTF-8 BOM so Windows Inno Setup reads Korean cleanly
    raw_bytes = guide_file.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")
    assert "Claude Code" in raw_bytes.decode("utf-8-sig")

    # Unregister via CLI
    rc_unreg = cli_main(["setup", "--unregister", "codex,gemini", "--json"])
    assert rc_unreg == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["status"] for item in payload["unregistered"]] == [
        "removed",
        "removed",
    ]


def test_register_preserves_custom_fields_in_existing_entry(tmp_path: Path) -> None:
    # 1. Claude Desktop: existing has custom env
    desktop_cfg = config_path_for(
        "claude-desktop", home=tmp_path, env={}, platform="darwin"
    )
    desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
    desktop_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "hwp-agent": {
                        "command": "/old/path/hwp-agent",
                        "args": ["mcp"],
                        "env": {"HWP_AGENT_PROFILE": "/custom/profile.json"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    # 2. Codex: existing has custom startup_timeout_sec and env table
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    codex_cfg.write_text(
        "[mcp_servers.hwp-agent]\n"
        'command = "/old/hwp-agent"\n'
        'args = ["mcp"]\n'
        "startup_timeout_sec = 60\n\n"
        "[mcp_servers.hwp-agent.env]\n"
        'MY_ENV = "value"\n',
        encoding="utf-8",
    )

    new_cmd = "/new/path/hwp-agent"
    actions = register_clients(
        ("claude-desktop", "codex"),
        command=new_cmd,
        home=tmp_path,
        env={},
        platform="darwin",
    )
    assert [a.status for a in actions] == ["updated", "updated"]

    # Verify JSON: command updated, env preserved!
    d_data = json.loads(desktop_cfg.read_text(encoding="utf-8"))
    assert d_data["mcpServers"]["hwp-agent"]["command"] == new_cmd
    assert d_data["mcpServers"]["hwp-agent"]["env"] == {
        "HWP_AGENT_PROFILE": "/custom/profile.json"
    }

    # Verify TOML: command updated, startup_timeout_sec & subtable preserved!
    c_data = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert c_data["mcp_servers"]["hwp-agent"]["command"] == new_cmd
    assert c_data["mcp_servers"]["hwp-agent"]["startup_timeout_sec"] == 60
    assert c_data["mcp_servers"]["hwp-agent"]["env"] == {"MY_ENV": "value"}


def test_unregister_matching_command_only(tmp_path: Path) -> None:
    desktop_cfg = config_path_for(
        "claude-desktop", home=tmp_path, env={}, platform="darwin"
    )
    desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
    desktop_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "hwp-agent": {
                        "command": "/installed/hwp-agent",
                        "args": ["mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    # Different command doesn't remove (noop)
    unreg_mismatch = unregister_clients(
        ("claude-desktop",),
        matching_command="/other/dev/hwp-agent",
        home=tmp_path,
        env={},
        platform="darwin",
    )
    assert unreg_mismatch[0].status == "noop"
    assert "hwp-agent" in json.loads(desktop_cfg.read_text(encoding="utf-8"))["mcpServers"]

    # Matching command removes
    unreg_match = unregister_clients(
        ("claude-desktop",),
        matching_command="/installed/hwp-agent",
        home=tmp_path,
        env={},
        platform="darwin",
    )
    assert unreg_match[0].status == "removed"
    assert "hwp-agent" not in json.loads(desktop_cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_register_updates_inline_and_dotted_codex_toml(tmp_path: Path) -> None:
    # 1. Inline table with trailing comment: hwp-agent = { ... } # installed manually
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    codex_cfg.write_text(
        '[mcp_servers]\n'
        'hwp-agent = { command = "/old/hwp-agent", args = ["mcp"] } # installed manually\n'
        'other = { command = "/bin/other" }\n',
        encoding="utf-8",
    )

    new_cmd = "/new/hwp-agent"
    actions = register_clients(
        ("codex",), command=new_cmd, home=tmp_path, env={}, platform="darwin"
    )
    assert actions[0].status == "updated"
    raw_updated = codex_cfg.read_text(encoding="utf-8")
    assert "# installed manually" in raw_updated
    parsed = tomllib.loads(raw_updated)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == new_cmd
    assert parsed["mcp_servers"]["hwp-agent"]["args"] == ["mcp"]
    # 1-b. Unregister removes the inline table and comment cleanly
    unreg = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg[0].status == "removed"
    raw_after = codex_cfg.read_text(encoding="utf-8")
    assert "# installed manually" not in raw_after
    parsed_after = tomllib.loads(raw_after)
    assert "hwp-agent" not in parsed_after["mcp_servers"]
    assert "other" in parsed_after["mcp_servers"]

    # 1-c. Nested inline table: env = { FOO = "bar" }
    codex_cfg.write_text(
        '[mcp_servers]\n'
        'hwp-agent = { command = "/old/hwp", env = { PROFILE = "default" } }\n'
        'other = { command = "/bin/other" }\n',
        encoding="utf-8",
    )
    actions_nested = register_clients(
        ("codex",), command=new_cmd, home=tmp_path, env={}, platform="darwin"
    )
    assert actions_nested[0].status == "updated"
    parsed_nested = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert parsed_nested["mcp_servers"]["hwp-agent"]["command"] == new_cmd
    assert parsed_nested["mcp_servers"]["hwp-agent"]["env"] == {"PROFILE": "default"}

    # Unregister removes the entire nested inline entry cleanly without leaving dangling braces
    unreg_nested = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg_nested[0].status == "removed"
    parsed_after_nested = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert "hwp-agent" not in parsed_after_nested["mcp_servers"]
    assert "other" in parsed_after_nested["mcp_servers"]

    # 1-d. Inline table immediately followed by a standalone user comment on the next line
    codex_cfg.write_text(
        '[mcp_servers]\n'
        'hwp-agent = { command = "/old/hwp" }\n'
        '# important user comment on next line\n'
        'other = { command = "/bin/other" }\n',
        encoding="utf-8",
    )
    unreg_standalone = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg_standalone[0].status == "removed"
    raw_after_standalone = codex_cfg.read_text(encoding="utf-8")
    assert "# important user comment on next line" in raw_after_standalone
    parsed_after_standalone = tomllib.loads(raw_after_standalone)
    assert "hwp-agent" not in parsed_after_standalone["mcp_servers"]
    assert "other" in parsed_after_standalone["mcp_servers"]

    # 1-e. Nested table containing its own 'command' key before the top-level command key
    codex_cfg.write_text(
        '[mcp_servers]\n'
        'hwp-agent = { env = { command = "value" }, command = "/old/hwp", args = ["mcp"] }\n',
        encoding="utf-8",
    )
    actions_nested_cmd = register_clients(
        ("codex",), command=new_cmd, home=tmp_path, env={}, platform="darwin"
    )
    assert actions_nested_cmd[0].status == "updated"
    parsed_nested_cmd = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert parsed_nested_cmd["mcp_servers"]["hwp-agent"]["env"]["command"] == "value"
    assert parsed_nested_cmd["mcp_servers"]["hwp-agent"]["command"] == new_cmd
    assert parsed_nested_cmd["mcp_servers"]["hwp-agent"]["args"] == ["mcp"]

    # 2. Table-scoped dotted keys under [mcp_servers]: hwp-agent.command = ...
    codex_cfg.write_text(
        '[mcp_servers]\n'
        'other = { command = "/bin/other" }\n'
        'hwp-agent.command = "/old/hwp-agent"\n'
        'hwp-agent.args = ["mcp"]\n',
        encoding="utf-8",
    )
    actions2 = register_clients(
        ("codex",), command=new_cmd, home=tmp_path, env={}, platform="darwin"
    )
    assert actions2[0].status == "updated"
    parsed2 = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert parsed2["mcp_servers"]["hwp-agent"]["command"] == new_cmd
    assert parsed2["mcp_servers"]["hwp-agent"]["args"] == ["mcp"]
    assert parsed2["mcp_servers"]["other"]["command"] == "/bin/other"

    # Unregister removes the scoped dotted keys cleanly
    unreg2 = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg2[0].status == "removed"
    parsed_after2 = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert "hwp-agent" not in parsed_after2["mcp_servers"]
    assert "other" in parsed_after2["mcp_servers"]


def test_cli_setup_force_with_json_keeps_stdout_clean(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    # Mock download_jar so we don't hit the network in test
    fake_jar = tmp_path / "fake.jar"
    fake_jar.write_bytes(b"jar")
    monkeypatch.setattr(
        "hwp_agent.convert.fetch_jar.download_jar", lambda **_kw: fake_jar
    )

    rc = cli_main(
        [
            "setup",
            "--force",
            "--json",
            "--register",
            "claude-code",
            "--command",
            "/bin/hwp-agent",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    # stdout MUST be pure, valid JSON (no "downloading converter jar..." noise)
    payload = json.loads(captured.out)
    assert "registered" in payload
    # Diagnostics must have gone to stderr
    assert "downloading converter jar" in captured.err or "ok: jar ready" in captured.err


def test_codex_toml_unicode_and_non_bmp_path(tmp_path: Path) -> None:
    """Non-BMP characters (e.g. emojis in paths) must serialize as valid TOML Unicode scalars."""
    codex_cfg = tmp_path / ".codex" / "config.toml"
    emoji_cmd = r"C:\Users\홍길동🚀\AppData\Local\Programs\hwp-agent\hwp-agent.exe"

    # 1. Register to a non-existent file
    res = register_clients(
        ("codex",),
        home=tmp_path,
        env={"USERPROFILE": str(tmp_path)},
        platform="win32",
        command=emoji_cmd,
    )
    assert res[0].status == "added"
    raw = codex_cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == emoji_cmd

    # 2. Update existing table with another non-BMP path
    emoji_cmd2 = r"C:\Users\홍길동🎉\hwp-agent.exe"
    res2 = register_clients(
        ("codex",),
        home=tmp_path,
        env={"USERPROFILE": str(tmp_path)},
        platform="win32",
        command=emoji_cmd2,
    )
    assert res2[0].status == "updated"
    raw2 = codex_cfg.read_text(encoding="utf-8")
    parsed2 = tomllib.loads(raw2)
    assert parsed2["mcp_servers"]["hwp-agent"]["command"] == emoji_cmd2

    # 3. Update inline table with non-BMP path
    codex_cfg.write_text(
        '[mcp_servers]\nhwp-agent = { command = "old", args = ["mcp"] }\n',
        encoding="utf-8",
    )
    res3 = register_clients(
        ("codex",),
        home=tmp_path,
        env={"USERPROFILE": str(tmp_path)},
        platform="win32",
        command=emoji_cmd,
    )
    assert res3[0].status == "updated"
    raw3 = codex_cfg.read_text(encoding="utf-8")
    parsed3 = tomllib.loads(raw3)
    assert parsed3["mcp_servers"]["hwp-agent"]["command"] == emoji_cmd


def test_codex_toml_header_comment(tmp_path: Path) -> None:
    """Comments following table headers (e.g. [mcp_servers.hwp-agent] # comment) must be handled."""
    codex_cfg = tmp_path / ".codex" / "config.toml"
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    codex_cfg.write_text(
        '[mcp_servers.hwp-agent] # installed manually\n'
        'command = "/old/path"\n'
        'args = ["mcp"]\n\n'
        '[other]\n'
        'key = "val"\n',
        encoding="utf-8",
    )

    # 1. Update must recognize existing table and not duplicate
    res = register_clients(
        ("codex",),
        home=tmp_path,
        env={},
        platform="darwin",
        command="/new/path",
    )
    assert res[0].status == "updated"
    raw = codex_cfg.read_text(encoding="utf-8")
    assert "# installed manually" in raw
    parsed = tomllib.loads(raw)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == "/new/path"
    assert parsed["other"]["key"] == "val"

    # 2. Unregister removes the section and preserves subsequent tables
    unreg = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg[0].status == "removed"
    raw_after = codex_cfg.read_text(encoding="utf-8")
    parsed_after = tomllib.loads(raw_after)
    assert "hwp-agent" not in parsed_after.get("mcp_servers", {})
    assert parsed_after["other"]["key"] == "val"


def test_register_and_unregister_with_utf8_bom(tmp_path: Path) -> None:
    """Configs written with a UTF-8 BOM (e.g. from PowerShell Out-File) must decode cleanly."""
    claude_cfg = config_path_for("claude-code", home=tmp_path, env={}, platform="darwin")
    claude_cfg.parent.mkdir(parents=True, exist_ok=True)
    # Write JSON with UTF-8 BOM
    bom_json = b'\xef\xbb\xbf{\n  "mcpServers": {\n    "other": {"command": "other"}\n  }\n}'
    claude_cfg.write_bytes(bom_json)

    # Register should succeed without JSONDecodeError
    res = register_clients(
        ("claude-code",),
        home=tmp_path,
        env={},
        platform="darwin",
        command="/bin/hwp-agent",
    )
    assert res[0].status == "added"
    data = json.loads(claude_cfg.read_text(encoding="utf-8-sig"))
    assert data["mcpServers"]["other"] == {"command": "other"}
    assert data["mcpServers"]["hwp-agent"]["command"] == "/bin/hwp-agent"

    # Unregister with BOM
    claude_cfg.write_bytes(
        b'\xef\xbb\xbf{\n  "mcpServers": {\n    "hwp-agent": {"command": "/bin/hwp-agent"}\n  }\n}'
    )
    unreg = unregister_clients(("claude-code",), home=tmp_path, env={}, platform="darwin")
    assert unreg[0].status == "removed"
    data_after = json.loads(claude_cfg.read_text(encoding="utf-8-sig"))
    assert "hwp-agent" not in data_after.get("mcpServers", {})

    # Also test TOML with UTF-8 BOM
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    bom_toml = b'\xef\xbb\xbf[mcp_servers]\nother = { command = "other" }\n'
    codex_cfg.write_bytes(bom_toml)

    res_toml = register_clients(
        ("codex",),
        home=tmp_path,
        env={},
        platform="darwin",
        command="/bin/hwp-agent",
    )
    assert res_toml[0].status == "added"
    parsed_toml = tomllib.loads(codex_cfg.read_text(encoding="utf-8-sig"))
    assert parsed_toml["mcp_servers"]["hwp-agent"]["command"] == "/bin/hwp-agent"


def test_claude_config_dir_resolution(tmp_path: Path) -> None:
    custom_dir = tmp_path / "custom_claude"
    custom_dir.mkdir(parents=True)
    cfg = config_path_for(
        "claude-code", home=tmp_path, env={"CLAUDE_CONFIG_DIR": str(custom_dir)}
    )
    assert cfg == custom_dir / ".claude.json"

    # Test is_client_installed with CLAUDE_CONFIG_DIR
    assert not is_client_installed(
        "claude-code",
        home=tmp_path,
        env={"CLAUDE_CONFIG_DIR": str(tmp_path / "nonexistent")},
        which_fn=lambda _: None,
    )
    assert is_client_installed(
        "claude-code",
        home=tmp_path,
        env={"CLAUDE_CONFIG_DIR": str(custom_dir)},
        which_fn=lambda _: None,
    )


def test_write_text_atomic_permissions(tmp_path: Path) -> None:
    import os
    import sys

    target = tmp_path / "secret.json"
    _write_text_atomic(target, '{"key": "val"}')
    assert target.read_text(encoding="utf-8") == '{"key": "val"}'

    if sys.platform != "win32":
        # Newly created file must be 0600
        mode = os.stat(target).st_mode & 0o777
        assert mode == 0o600

        # Updating existing file must preserve its permissions
        os.chmod(target, 0o640)
        _write_text_atomic(target, '{"key": "new_val"}')
        assert target.read_text(encoding="utf-8") == '{"key": "new_val"}'


def test_write_text_atomic_preserves_symlink(tmp_path: Path) -> None:
    real_file = tmp_path / "dotfiles" / "config.toml"
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_text("initial = true\n", encoding="utf-8")

    symlink_file = tmp_path / ".codex" / "config.toml"
    symlink_file.parent.mkdir(parents=True, exist_ok=True)
    symlink_file.symlink_to(real_file)

    assert symlink_file.is_symlink()
    _write_text_atomic(symlink_file, "updated = true\n")

    # Symlink must NOT be broken or replaced by a normal file
    assert symlink_file.is_symlink()
    assert real_file.read_text(encoding="utf-8") == "updated = true\n"


def test_jsonc_with_comments_support(tmp_path: Path) -> None:
    gemini_cfg = config_path_for("gemini", home=tmp_path, env={}, platform="darwin")
    gemini_cfg.parent.mkdir(parents=True, exist_ok=True)
    jsonc_content = (
        "// Global Gemini configuration\n"
        "{\n"
        "  /* existing mcp servers */\n"
        '  "mcpServers": {\n'
        '    "other": {"command": "other"}\n'
        "  }\n"
        "}\n"
    )
    gemini_cfg.write_text(jsonc_content, encoding="utf-8")

    res = register_clients(
        ("gemini",), home=tmp_path, env={}, platform="darwin", command="/bin/hwp-agent"
    )
    assert res[0].status == "added"
    parsed = json.loads(gemini_cfg.read_text(encoding="utf-8"))
    assert parsed["mcpServers"]["hwp-agent"]["command"] == "/bin/hwp-agent"
    assert parsed["mcpServers"]["other"]["command"] == "other"


def test_per_client_fault_tolerance(tmp_path: Path, monkeypatch, capsys) -> None:
    # Set up one broken client config and two valid ones
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / ".gemini"))

    broken_codex = tmp_path / ".codex" / "config.toml"
    broken_codex.parent.mkdir(parents=True, exist_ok=True)
    broken_codex.write_text("[mcp_servers]\nthis is not valid toml = = = \n", encoding="utf-8")

    # When registering all three, codex errors, but claude-code and gemini succeed
    actions = register_clients(
        ("claude-code", "codex", "gemini"),
        home=tmp_path,
        env={"CODEX_HOME": str(tmp_path / ".codex"), "GEMINI_CLI_HOME": str(tmp_path / ".gemini")},
        platform="darwin",
        command="/bin/hwp-agent",
    )
    assert len(actions) == 3
    assert actions[0].status == "added"  # claude-code
    assert actions[1].status == "error"  # codex
    assert actions[1].detail != ""
    assert actions[2].status == "added"  # gemini

    # CLI returns 2 due to error, but still outputs report and guide
    rc = cli_main([
        "setup",
        "--register",
        "claude-code,codex,gemini",
        "--command",
        "/bin/hwp-agent",
    ])
    assert rc == 2
    out = capsys.readouterr().out
    assert "[MCP 등록 결과]" in out
    assert "실패" in out
    assert "이미 등록됨" in out


def test_codex_unregister_preserves_trailing_comments_and_other_tables(
    tmp_path: Path,
) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[other_table]\n"
        'hwp-agent = { test = "do not delete" }\n\n'
        "[mcp_servers.hwp-agent]\n"
        'command = "/bin/hwp-agent"\n'
        'args = ["mcp"]\n\n'
        "# Important comment for spacemath server\n"
        "[mcp_servers.spacemath]\n"
        'command = "/bin/spacemath"\n'
    )
    codex_cfg.write_text(content, encoding="utf-8")

    unreg = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg[0].status == "removed"

    result = codex_cfg.read_text(encoding="utf-8")
    # Must preserve comments for spacemath
    assert "# Important comment for spacemath server" in result
    assert "[mcp_servers.spacemath]" in result
    # Must preserve other_table's hwp-agent key
    assert 'hwp-agent = { test = "do not delete" }' in result
    # mcp_servers.hwp-agent must be gone
    parsed = tomllib.loads(result)
    assert "hwp-agent" not in parsed["mcp_servers"]
    assert parsed["other_table"]["hwp-agent"] == {"test": "do not delete"}


def test_codex_update_multiline_args(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[mcp_servers.hwp-agent]\n"
        'command = "/old/hwp-agent"\n'
        "args = [\n"
        '    "mcp",\n'
        '    "--verbose",\n'
        "]\n"
    )
    codex_cfg.write_text(content, encoding="utf-8")

    res = register_clients(
        ("codex",), home=tmp_path, env={}, platform="darwin", command="/new/hwp-agent"
    )
    assert res[0].status == "updated"

    result = codex_cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(result)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == "/new/hwp-agent"
    assert parsed["mcp_servers"]["hwp-agent"]["args"] == ["mcp"]


def test_codex_dotted_keys_preserve_custom_fields(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[mcp_servers]\n"
        'other = { command = "/bin/other" }\n'
        'hwp-agent.command = "/old/hwp-agent"\n'
        'hwp-agent.args = ["mcp"]\n'
        "hwp-agent.startup_timeout_sec = 45\n"
        'hwp-agent.env = { CUSTOM_KEY = "custom_val" }\n'
    )
    codex_cfg.write_text(content, encoding="utf-8")

    res = register_clients(
        ("codex",), home=tmp_path, env={}, platform="darwin", command="/new/hwp-agent"
    )
    assert res[0].status == "updated"

    parsed = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    entry = parsed["mcp_servers"]["hwp-agent"]
    assert entry["command"] == "/new/hwp-agent"
    assert entry["args"] == ["mcp"]
    assert entry["startup_timeout_sec"] == 45
    assert entry["env"] == {"CUSTOM_KEY": "custom_val"}
    assert parsed["mcp_servers"]["other"]["command"] == "/bin/other"


def test_jsonc_creates_bak_backup_on_modification(tmp_path: Path) -> None:
    gemini_cfg = config_path_for("gemini", home=tmp_path, env={}, platform="darwin")
    gemini_cfg.parent.mkdir(parents=True, exist_ok=True)
    jsonc_raw = '// My precious comment\n{\n  "mcpServers": {}\n}\n'
    gemini_cfg.write_text(jsonc_raw, encoding="utf-8")

    res = register_clients(
        ("gemini",), home=tmp_path, env={}, platform="darwin", command="/bin/hwp-agent"
    )
    assert res[0].status == "added"
    assert "원본 백업" in res[0].detail

    bak_file = gemini_cfg.with_name(gemini_cfg.name + ".bak")
    assert bak_file.is_file()
    assert "// My precious comment" in bak_file.read_text(encoding="utf-8")


def test_codex_register_does_not_touch_other_table_inline(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[other_table]\n"
        'hwp-agent = { test = "keep this untouched" }\n\n'
        "[mcp_servers]\n"
        'other = { command = "/bin/other" }\n'
    )
    codex_cfg.write_text(content, encoding="utf-8")

    res = register_clients(
        ("codex",), home=tmp_path, env={}, platform="darwin", command="/bin/hwp-agent"
    )
    assert res[0].status == "added"

    result = codex_cfg.read_text(encoding="utf-8")
    # other_table must be preserved
    assert 'hwp-agent = { test = "keep this untouched" }' in result
    parsed = tomllib.loads(result)
    assert parsed["other_table"]["hwp-agent"] == {"test": "keep this untouched"}
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == "/bin/hwp-agent"


def test_codex_remove_sections_preserves_comments_boundary(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    # Example 1: section with trailing comments for next table
    content = (
        "[mcp_servers.hwp-agent]\n"
        'command = "/bin/hwp-agent"\n'
        'args = ["mcp"]\n\n'
        "# --- other servers ---\n"
        "[mcp_servers.spacemath]\n"
        'command = "/bin/spacemath"\n'
    )
    codex_cfg.write_text(content, encoding="utf-8")

    unreg = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg[0].status == "removed"

    result = codex_cfg.read_text(encoding="utf-8")
    assert "# --- other servers ---" in result
    assert "[mcp_servers.spacemath]" in result
    parsed = tomllib.loads(result)
    assert "hwp-agent" not in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["spacemath"]["command"] == "/bin/spacemath"


def test_codex_dotted_keys_quoted_keys_and_nested(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[mcp_servers]\n"
        'hwp-agent.command = "/old/hwp-agent"\n'
        'hwp-agent.args = ["mcp"]\n'
        'hwp-agent.env."MY.SPECIAL.VAR" = "val1"\n'
        'hwp-agent.nested = { deep = { num = 42 } }\n'
    )
    codex_cfg.write_text(content, encoding="utf-8")

    res = register_clients(
        ("codex",), home=tmp_path, env={}, platform="darwin", command="/new/hwp-agent"
    )
    assert res[0].status == "updated"

    result = codex_cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(result)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == "/new/hwp-agent"
    assert parsed["mcp_servers"]["hwp-agent"]["env"]["MY.SPECIAL.VAR"] == "val1"
    assert parsed["mcp_servers"]["hwp-agent"]["nested"]["deep"]["num"] == 42


def test_codex_quoted_toml_path_segments(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        '["mcp_servers"."hwp-agent"]\n'
        'command = "/old/hwp-agent"\n'
        'args = ["mcp"]\n'
        'env = { FOO = "bar" }\n'
    )
    codex_cfg.write_text(content, encoding="utf-8")

    # Registration updates without creating duplicate table
    res = register_clients(
        ("codex",), home=tmp_path, env={}, platform="darwin", command="/new/hwp-agent"
    )
    assert res[0].status == "updated"

    result = codex_cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(result)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == "/new/hwp-agent"
    assert parsed["mcp_servers"]["hwp-agent"]["env"]["FOO"] == "bar"

    # Unregistration removes quoted table cleanly
    unreg = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg[0].status == "removed"
    raw_cfg = codex_cfg.read_text(encoding="utf-8")
    parsed_after = tomllib.loads(raw_cfg) if raw_cfg.strip() else {}
    assert "hwp-agent" not in parsed_after.get("mcp_servers", {})


def test_codex_inline_table_with_braces_in_comment(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[mcp_servers]\n"
        'hwp-agent = { command = "/old/hwp-agent", args = ["mcp"] } '
        "# note: { some_template_key } and { extra }\n"
        'other = { command = "/bin/other" }\n'
    )
    codex_cfg.write_text(content, encoding="utf-8")

    res = register_clients(
        ("codex",), home=tmp_path, env={}, platform="darwin", command="/new/hwp-agent"
    )
    assert res[0].status == "updated"

    result = codex_cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(result)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == "/new/hwp-agent"
    assert "# note: { some_template_key } and { extra }" in result


def test_codex_preserve_datetime_custom_fields(tmp_path: Path) -> None:
    codex_cfg = config_path_for("codex", home=tmp_path, env={}, platform="darwin")
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[mcp_servers]\n"
        'hwp-agent.command = "/old/hwp-agent"\n'
        'hwp-agent.args = ["mcp"]\n'
        "hwp-agent.installed_at = 2026-10-16T12:30:00Z\n"
        "hwp-agent.installed_date = 2026-10-16\n"
        "hwp-agent.installed_time = 12:30:00\n"
    )
    codex_cfg.write_text(content, encoding="utf-8")

    res = register_clients(
        ("codex",), home=tmp_path, env={}, platform="darwin", command="/new/hwp-agent"
    )
    assert res[0].status == "updated"

    result = codex_cfg.read_text(encoding="utf-8")
    parsed = tomllib.loads(result)
    assert parsed["mcp_servers"]["hwp-agent"]["command"] == "/new/hwp-agent"
    assert parsed["mcp_servers"]["hwp-agent"]["installed_date"] == datetime.date(2026, 10, 16)


