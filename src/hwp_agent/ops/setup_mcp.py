"""Register/unregister ``hwp-agent mcp`` across local AI clients (issue #29).

Supports the four local stdio MCP clients defined in ``ji-deploy-standards``:

* ``claude-desktop`` — Claude Desktop (``claude_desktop_config.json``)
* ``claude-code``    — Claude Code CLI (``~/.claude.json``)
* ``codex``          — OpenAI Codex CLI/App (``~/.codex/config.toml``)
* ``gemini``         — Google Gemini CLI (``~/.gemini/settings.json``)

All operations are idempotent: existing entries for ``hwp-agent`` are updated in
place (or left untouched if already identical), and unrelated MCP servers or
settings in the same file are preserved.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SERVER_NAME = "hwp-agent"

CLIENT_ORDER: tuple[str, ...] = (
    "claude-desktop",
    "claude-code",
    "codex",
    "gemini",
)

CLIENT_LABELS: dict[str, str] = {
    "claude-desktop": "Claude Desktop",
    "claude-code": "Claude Code",
    "codex": "Codex",
    "gemini": "Gemini CLI",
}

CLIENT_ALIASES: dict[str, tuple[str, ...]] = {
    "claude-desktop": ("claude-desktop",),
    "desktop": ("claude-desktop",),
    "claudedesktop": ("claude-desktop",),
    "claude-code": ("claude-code",),
    "code": ("claude-code",),
    "claudecode": ("claude-code",),
    # Shorthand `claude` covers both Desktop and Code so
    # `hwp-agent setup --register claude,codex,gemini` connects all four.
    "claude": ("claude-desktop", "claude-code"),
    "codex": ("codex",),
    "gemini": ("gemini",),
    "gemini-cli": ("gemini",),
    "geminicli": ("gemini",),
    "all": CLIENT_ORDER,
}

CLIENT_USAGE_GUIDES: dict[str, tuple[str, ...]] = {
    "claude-desktop": (
        "앱을 완전히 종료한 뒤 재시작하고, 채팅에 로컬 HWP/HWPX 경로를 알려준 뒤",
        '"바탕화면의 등록신청서.hwp를 내 정보로 채워줘"라고 요청하세요.',
    ),
    "claude-code": (
        "터미널에서 `claude`를 실행(켜져 있다면 재시작, `/mcp`로 연결 확인)하고",
        '"이 폴더의 서식.hwpx를 내 프로필로 채워줘"라고 요청하세요.',
    ),
    "codex": (
        "터미널에서 `codex`를 실행하고 같은 식으로 로컬 한글 파일 작업을 요청하세요.",
    ),
    "gemini": (
        "터미널에서 `gemini`를 실행(`/mcp`로 hwp-agent 확인)하고 같은 식으로 요청하세요.",
    ),
}

_STATUS_KO: dict[str, str] = {
    "added": "등록됨",
    "updated": "갱신됨",
    "unchanged": "이미 등록됨 (건너뜀)",
    "removed": "등록 해제됨",
    "noop": "등록 항목 없음 (건너뜀)",
}

# Matches [mcp_servers.hwp-agent] or [mcp_servers."hwp-agent"] (and any sub-tables)
# up to the next top-level table header or end of file.
_CODEX_SECTION_RE = re.compile(
    r"(?ms)^\[mcp_servers\.(?:hwp-agent|\"hwp-agent\"|'hwp-agent')(?:\.[^\]]+)?\][ \t]*(?:#[^\r\n]*)?\r?\n.*?(?=^\[|\Z)"
)


@dataclass(frozen=True)
class RegistrationAction:
    client: str
    label: str
    config_path: Path
    status: str  # "added" | "updated" | "unchanged" | "removed" | "noop"
    command: str = ""
    args: tuple[str, ...] = ("mcp",)

    def as_dict(self) -> dict[str, object]:
        return {
            "client": self.client,
            "label": self.label,
            "config_path": str(self.config_path),
            "status": self.status,
            "command": self.command,
            "args": list(self.args),
        }


def resolve_mcp_command(explicit: str | Path | None = None) -> str:
    """Determine the executable path to write into MCP client configs."""
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    which = shutil.which("hwp-agent")
    if which:
        return str(Path(which).absolute())
    exe_name = "hwp-agent.exe" if os.name == "nt" else "hwp-agent"
    sibling = Path(sys.executable).with_name(exe_name)
    if sibling.is_file():
        return str(sibling.absolute())
    return "hwp-agent"


def config_path_for(
    client: str,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Return the MCP config file path for ``client`` on the target platform."""
    resolved_home = Path(home) if home is not None else Path.home()
    resolved_env = env if env is not None else os.environ
    resolved_plat = platform if platform is not None else sys.platform

    if client == "claude-desktop":
        if resolved_plat.startswith("win"):
            appdata = resolved_env.get("APPDATA") or str(
                resolved_home / "AppData" / "Roaming"
            )
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
        if resolved_plat == "darwin":
            return (
                resolved_home
                / "Library"
                / "Application Support"
                / "Claude"
                / "claude_desktop_config.json"
            )
        xdg = resolved_env.get("XDG_CONFIG_HOME") or str(resolved_home / ".config")
        return Path(xdg) / "Claude" / "claude_desktop_config.json"

    if client == "claude-code":
        override = resolved_env.get("CLAUDE_CONFIG_FILE")
        if override:
            return Path(override)
        return resolved_home / ".claude.json"

    if client == "codex":
        codex_home = resolved_env.get("CODEX_HOME") or str(resolved_home / ".codex")
        return Path(codex_home) / "config.toml"

    if client == "gemini":
        gemini_home = resolved_env.get("GEMINI_CLI_HOME") or str(
            resolved_home / ".gemini"
        )
        return Path(gemini_home) / "settings.json"

    raise ValueError(f"unknown client: {client!r}")


def is_client_installed(
    client: str,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which_fn=shutil.which,
) -> bool:
    """Heuristic check for whether an AI client is installed on this machine."""
    resolved_home = Path(home) if home is not None else Path.home()
    resolved_env = env if env is not None else os.environ
    resolved_plat = platform if platform is not None else sys.platform
    cfg = config_path_for(
        client, home=resolved_home, env=resolved_env, platform=resolved_plat
    )

    if client == "claude-desktop":
        if cfg.exists() or cfg.parent.is_dir():
            return True
        if resolved_plat == "darwin":
            return (
                Path("/Applications/Claude.app").exists()
                or (resolved_home / "Applications" / "Claude.app").exists()
            )
        if resolved_plat.startswith("win"):
            localappdata = Path(
                resolved_env.get("LOCALAPPDATA")
                or (resolved_home / "AppData" / "Local")
            )
            return (
                (localappdata / "AnthropicClaude").is_dir()
                or (localappdata / "Programs" / "Claude").is_dir()
            )
        return False

    if client == "claude-code":
        if cfg.exists() or (resolved_home / ".claude").is_dir():
            return True
        if which_fn("claude") is not None:
            return True
        if resolved_plat.startswith("win"):
            appdata = Path(
                resolved_env.get("APPDATA") or (resolved_home / "AppData" / "Roaming")
            )
            return (
                (resolved_home / ".local" / "bin" / "claude.exe").is_file()
                or (appdata / "npm" / "claude.cmd").is_file()
            )
        return False

    if client == "codex":
        if cfg.exists() or cfg.parent.is_dir():
            return True
        if which_fn("codex") is not None:
            return True
        if resolved_plat == "darwin" and Path("/Applications/Codex.app").exists():
            return True
        if resolved_plat.startswith("win"):
            appdata = Path(
                resolved_env.get("APPDATA") or (resolved_home / "AppData" / "Roaming")
            )
            return (appdata / "npm" / "codex.cmd").is_file()
        return False

    if client == "gemini":
        if cfg.exists() or cfg.parent.is_dir():
            return True
        if which_fn("gemini") is not None:
            return True
        if resolved_plat.startswith("win"):
            appdata = Path(
                resolved_env.get("APPDATA") or (resolved_home / "AppData" / "Roaming")
            )
            return (appdata / "npm" / "gemini.cmd").is_file()
        return False

    raise ValueError(f"unknown client: {client!r}")


def detect_installed_clients(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which_fn=shutil.which,
) -> tuple[str, ...]:
    """Return canonical IDs of AI clients detected on the machine."""
    return tuple(
        c
        for c in CLIENT_ORDER
        if is_client_installed(
            c, home=home, env=env, platform=platform, which_fn=which_fn
        )
    )


def resolve_targets(
    specs: Sequence[str] | str,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which_fn=shutil.which,
) -> tuple[str, ...]:
    """Expand comma-separated client specs into canonical ``CLIENT_ORDER`` IDs."""
    raw_items = [specs] if isinstance(specs, str) else list(specs)
    tokens: list[str] = []
    for item in raw_items:
        for part in item.split(","):
            tok = part.strip().lower()
            if tok:
                tokens.append(tok)

    if not tokens:
        raise ValueError(
            "클라이언트 이름을 지정하세요 (예: claude,codex,gemini 또는 all, auto)"
        )

    chosen: list[str] = []
    for tok in tokens:
        if tok == "auto":
            for c in detect_installed_clients(
                home=home, env=env, platform=platform, which_fn=which_fn
            ):
                if c not in chosen:
                    chosen.append(c)
            continue
        mapped = CLIENT_ALIASES.get(tok)
        if mapped is None:
            valid = ", ".join((*CLIENT_ORDER, "claude", "all", "auto"))
            raise ValueError(f"알 수 없는 클라이언트: {tok!r} (지원: {valid})")
        for c in mapped:
            if c not in chosen:
                chosen.append(c)

    return tuple(c for c in CLIENT_ORDER if c in chosen)


def _desired_json_entry(client: str, command: str) -> dict[str, object]:
    if client == "claude-code":
        return {"type": "stdio", "command": command, "args": ["mcp"]}
    return {"command": command, "args": ["mcp"]}


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _is_same_command(actual: object, expected: str | None) -> bool:
    if expected is None:
        return True
    if not isinstance(actual, str):
        return False
    if actual == expected:
        return True
    try:
        norm_actual = os.path.normcase(os.path.abspath(actual))
        norm_expected = os.path.normcase(os.path.abspath(expected))
        return norm_actual == norm_expected
    except Exception:
        return False


def _register_json_client(
    client: str, path: Path, command: str, *, dry_run: bool = False
) -> str:
    desired = _desired_json_entry(client, command)
    if not path.is_file():
        data: dict[str, object] = {}
    else:
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            data = {}
        else:
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError(f"{path}: JSON 최상위 구조가 객체(dict)가 아닙니다")
            data = loaded

    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    elif not isinstance(servers, dict):
        raise ValueError(f"{path}: 'mcpServers' 항목이 객체(dict)가 아닙니다")

    existing = servers.get(SERVER_NAME)
    if isinstance(existing, dict):
        updated = dict(existing)
        updated.update(desired)
        if updated == existing:
            return "unchanged"
        servers[SERVER_NAME] = updated
        status = "updated"
    else:
        servers[SERVER_NAME] = desired
        status = "added"

    if not dry_run:
        _write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return status


def _unregister_json_client(
    path: Path, *, matching_command: str | None = None, dry_run: bool = False
) -> str:
    if not path.is_file():
        return "noop"
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        return "noop"
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: JSON 최상위 구조가 객체(dict)가 아닙니다")
    servers = loaded.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return "noop"

    entry = servers[SERVER_NAME]
    if matching_command is not None:
        entry_cmd = entry.get("command") if isinstance(entry, dict) else None
        if not _is_same_command(entry_cmd, matching_command):
            return "noop"

    del servers[SERVER_NAME]
    if not dry_run:
        _write_text_atomic(
            path, json.dumps(loaded, ensure_ascii=False, indent=2) + "\n"
        )
    return "removed"


def _toml_string(val: str) -> str:
    """Serialize a string as a TOML-safe literal UTF-8 string with escaped controls."""
    return json.dumps(val, ensure_ascii=False)


def _codex_section_text(command: str) -> str:
    return (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f"command = {_toml_string(command)}\n"
        f'args = ["mcp"]\n'
    )


_CODEX_MAIN_TABLE_RE = re.compile(
    r"(?m)^\[mcp_servers\.(?:hwp-agent|\"hwp-agent\"|'hwp-agent')\][ \t]*(?:#[^\r\n]*)?\r?$"
)
_CODEX_INLINE_RE = re.compile(
    r"(?m)^([ \t]*(?:hwp-agent|\"hwp-agent\"|'hwp-agent')[ \t]*=[ \t]*\{)(.*)(\}[ \t]*(?:#[^\r\n]*)?)\r?$"
)
_CODEX_INLINE_LINE_RE = re.compile(
    r"(?m)^[ \t]*(?:hwp-agent|\"hwp-agent\"|'hwp-agent')[ \t]*=[ \t]*\{.*\}[ \t]*(?:#[^\r\n]*)?\r?\n?"
)
_CODEX_GLOBAL_DOTTED_RE = re.compile(
    r"(?m)^[ \t]*mcp_servers\.(?:hwp-agent|\"hwp-agent\"|'hwp-agent')"
    r"(?:\.[a-zA-Z0-9_-]+)*[ \t]*=.*$\r?\n?"
)
_CODEX_MCP_TABLE_RE = re.compile(
    r"(?m)^\[mcp_servers\][ \t]*(?:#[^\r\n]*)?\r?$"
)
_CODEX_SCOPED_DOTTED_RE = re.compile(
    r"(?m)^[ \t]*(?:hwp-agent|\"hwp-agent\"|'hwp-agent')"
    r"(?:\.[a-zA-Z0-9_-]+)+[ \t]*=.*$\r?\n?"
)


def _clean_codex_dotted_keys(raw: str) -> str:
    cleaned = _CODEX_GLOBAL_DOTTED_RE.sub("", raw)
    m = _CODEX_MCP_TABLE_RE.search(cleaned)
    if m:
        header_end = m.end()
        next_m = re.search(r"(?m)^\[", cleaned[header_end:])
        sec_end = header_end + next_m.start() if next_m else len(cleaned)
        sec_body = cleaned[header_end:sec_end]
        cleaned_sec = _CODEX_SCOPED_DOTTED_RE.sub("", sec_body)
        cleaned = cleaned[:header_end] + cleaned_sec + cleaned[sec_end:]
    return cleaned


def _update_inline_table(line: str, command: str) -> str:
    cmd_str = f"command = {_toml_string(command)}"
    args_str = 'args = ["mcp"]'
    m = _CODEX_INLINE_RE.match(line)
    if not m:
        return line
    prefix, inner, suffix = m.groups()
    if re.search(r"\bcommand\s*=", inner):
        inner = re.sub(
            r"\bcommand\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)",
            lambda _: cmd_str,
            inner,
            count=1,
        )
    else:
        inner = f" {cmd_str}," + inner
    if re.search(r"\bargs\s*=", inner):
        inner = re.sub(r"\bargs\s*=\s*\[[^\]]*\]", lambda _: args_str, inner, count=1)
    else:
        inner = inner.rstrip() + f", {args_str} "
    return f"{prefix}{inner}{suffix}"


def _update_codex_main_section(raw: str, command: str) -> str:
    match = _CODEX_MAIN_TABLE_RE.search(raw)
    if match:
        header_end = match.end()
        next_match = re.search(r"(?m)^\[", raw[header_end:])
        sec_end = header_end + next_match.start() if next_match else len(raw)

        sec_body = raw[header_end:sec_end]
        cmd_str = f"command = {_toml_string(command)}"
        args_str = 'args = ["mcp"]'

        if re.search(r"(?m)^[ \t]*command[ \t]*=.*$", sec_body):
            sec_body = re.sub(
                r"(?m)^[ \t]*command[ \t]*=.*$", lambda _: cmd_str, sec_body, count=1
            )
        else:
            sec_body = f"\n{cmd_str}" + sec_body

        if re.search(r"(?m)^[ \t]*args[ \t]*=.*$", sec_body):
            sec_body = re.sub(
                r"(?m)^[ \t]*args[ \t]*=.*$", lambda _: args_str, sec_body, count=1
            )
        else:
            if cmd_str in sec_body:
                sec_body = sec_body.replace(cmd_str, f"{cmd_str}\n{args_str}", 1)
            else:
                sec_body = f"\n{args_str}" + sec_body

        return raw[:header_end] + sec_body + raw[sec_end:]

    inline_match = _CODEX_INLINE_RE.search(raw)
    if inline_match:
        updated_line = _update_inline_table(inline_match.group(0), command)
        return raw[: inline_match.start()] + updated_line + raw[inline_match.end() :]

    cleaned = _clean_codex_dotted_keys(raw)
    sep = "" if cleaned.endswith("\n\n") else ("\n" if cleaned.endswith("\n") else "\n\n")
    return cleaned + sep + _codex_section_text(command)


def _register_codex_toml(path: Path, command: str, *, dry_run: bool = False) -> str:
    block = _codex_section_text(command)

    if not path.is_file():
        # Validate that the generated block is valid TOML before writing
        tomllib.loads(block)
        if not dry_run:
            _write_text_atomic(path, block)
        return "added"

    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        # Validate that the generated block is valid TOML before writing
        tomllib.loads(block)
        if not dry_run:
            _write_text_atomic(path, block)
        return "added"

    parsed = tomllib.loads(raw)
    mcp_servers = parsed.get("mcp_servers")
    if mcp_servers is not None and not isinstance(mcp_servers, dict):
        raise ValueError(f"{path}: 'mcp_servers' 항목이 테이블이 아닙니다")

    existing = mcp_servers.get(SERVER_NAME) if isinstance(mcp_servers, dict) else None
    if (
        isinstance(existing, dict)
        and existing.get("command") == command
        and existing.get("args") == ["mcp"]
    ):
        return "unchanged"

    status = "updated" if isinstance(existing, dict) else "added"
    rebuilt = _update_codex_main_section(raw, command)

    # Verify the resulting TOML parses cleanly and has the expected entry.
    check = tomllib.loads(rebuilt)
    check_entry = check.get("mcp_servers", {}).get(SERVER_NAME)
    if (
        not isinstance(check_entry, dict)
        or check_entry.get("command") != command
        or check_entry.get("args") != ["mcp"]
    ):
        raise RuntimeError(f"{path}: TOML 갱신 후 검증에 실패했습니다")

    if not dry_run:
        _write_text_atomic(path, rebuilt)
    return status


def _unregister_codex_toml(
    path: Path, *, matching_command: str | None = None, dry_run: bool = False
) -> str:
    if not path.is_file():
        return "noop"
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return "noop"
    parsed = tomllib.loads(raw)
    mcp_servers = parsed.get("mcp_servers")
    if not isinstance(mcp_servers, dict) or SERVER_NAME not in mcp_servers:
        return "noop"

    if matching_command is not None:
        entry = mcp_servers[SERVER_NAME]
        entry_cmd = entry.get("command") if isinstance(entry, dict) else None
        if not _is_same_command(entry_cmd, matching_command):
            return "noop"

    rebuilt = _CODEX_SECTION_RE.sub("", raw)
    rebuilt = _CODEX_INLINE_LINE_RE.sub("", rebuilt)
    rebuilt = _clean_codex_dotted_keys(rebuilt)
    rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
    if rebuilt and not rebuilt.endswith("\n"):
        rebuilt += "\n"

    check = tomllib.loads(rebuilt) if rebuilt.strip() else {}
    if SERVER_NAME in (check.get("mcp_servers") or {}):
        raise RuntimeError(f"{path}: TOML에서 {SERVER_NAME} 항목을 제거하지 못했습니다")

    if not dry_run:
        _write_text_atomic(path, rebuilt)
    return "removed"


def register_clients(
    clients: Sequence[str],
    *,
    command: str | Path | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    dry_run: bool = False,
) -> list[RegistrationAction]:
    """Idempotently register ``hwp-agent mcp`` in each requested client's config."""
    resolved_cmd = resolve_mcp_command(command)
    actions: list[RegistrationAction] = []
    for client in clients:
        cfg = config_path_for(client, home=home, env=env, platform=platform)
        if client == "codex":
            status = _register_codex_toml(cfg, resolved_cmd, dry_run=dry_run)
        else:
            status = _register_json_client(client, cfg, resolved_cmd, dry_run=dry_run)
        actions.append(
            RegistrationAction(
                client=client,
                label=CLIENT_LABELS[client],
                config_path=cfg,
                status=status,
                command=resolved_cmd,
            )
        )
    return actions


def unregister_clients(
    clients: Sequence[str],
    *,
    matching_command: str | Path | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    dry_run: bool = False,
) -> list[RegistrationAction]:
    """Idempotently remove ``hwp-agent`` from each requested client's MCP config.

    If ``matching_command`` is specified, only entries whose command matches
    it will be removed (protecting other installations/manual setups).
    """
    resolved_matching = (
        str(matching_command).strip() if matching_command is not None else None
    )
    actions: list[RegistrationAction] = []
    for client in clients:
        cfg = config_path_for(client, home=home, env=env, platform=platform)
        if client == "codex":
            status = _unregister_codex_toml(
                cfg, matching_command=resolved_matching, dry_run=dry_run
            )
        else:
            status = _unregister_json_client(
                cfg, matching_command=resolved_matching, dry_run=dry_run
            )
        actions.append(
            RegistrationAction(
                client=client,
                label=CLIENT_LABELS[client],
                config_path=cfg,
                status=status,
            )
        )
    return actions


def format_registration_report(actions: Sequence[RegistrationAction]) -> str:
    """Format the registration summary + client-specific usage guide.

    Shared by the CLI terminal output and the Windows Inno Setup completion page.
    Only the clients included in ``actions`` are shown in the usage guide.
    """
    lines: list[str] = ["[MCP 등록 결과]"]
    for act in actions:
        status_ko = _STATUS_KO.get(act.status, act.status)
        lines.append(f"  - {act.label}: {status_ko} ({act.config_path})")

    lines.append("")
    lines.append("[이렇게 쓰세요]")
    for act in actions:
        lines.append(f"  • {act.label}")
        for hint in CLIENT_USAGE_GUIDES.get(act.client, ()):
            lines.append(f"    {hint}")
    lines.append("  • ChatGPT · Gemini 웹/앱 (참고)")
    lines.append(
        "    소비자 웹·앱은 로컬 MCP를 지원하지 않으므로 "
        "웹 링크(https://hwp.namun.net)를 이용하세요."
    )
    return "\n".join(lines)


def format_unregistration_report(actions: Sequence[RegistrationAction]) -> str:
    """Format the unregistration summary."""
    lines: list[str] = ["[MCP 등록 해제 결과]"]
    for act in actions:
        status_ko = _STATUS_KO.get(act.status, act.status)
        lines.append(f"  - {act.label}: {status_ko} ({act.config_path})")
    return "\n".join(lines)
