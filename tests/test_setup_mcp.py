"""Tests for ``hwp-agent setup --register / --unregister`` (issue #29)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from hwp_agent.cli.main import main as cli_main
from hwp_agent.ops.setup_mcp import (
    CLIENT_ORDER,
    config_path_for,
    detect_installed_clients,
    format_registration_report,
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
    monkeypatch.setenv("CLAUDE_CONFIG_FILE", str(tmp_path / ".claude.json"))

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
    assert parsed["mcp_servers"]["other"]["command"] == "/bin/other"

    # Unregister removes the inline table and comment cleanly
    unreg = unregister_clients(("codex",), home=tmp_path, env={}, platform="darwin")
    assert unreg[0].status == "removed"
    raw_after = codex_cfg.read_text(encoding="utf-8")
    assert "# installed manually" not in raw_after
    parsed_after = tomllib.loads(raw_after)
    assert "hwp-agent" not in parsed_after["mcp_servers"]
    assert "other" in parsed_after["mcp_servers"]


def test_cli_setup_force_with_json_keeps_stdout_clean(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_FILE", str(tmp_path / ".claude.json"))

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

