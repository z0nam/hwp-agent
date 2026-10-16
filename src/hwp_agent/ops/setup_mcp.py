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

import datetime
import json
import os
import re
import shutil
import sys
import tempfile
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
    "error": "실패",
}


@dataclass(frozen=True)
class RegistrationAction:
    client: str
    label: str
    config_path: Path
    status: str  # "added" | "updated" | "unchanged" | "removed" | "noop" | "error"
    command: str = ""
    args: tuple[str, ...] = ("mcp",)
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "client": self.client,
            "label": self.label,
            "config_path": str(self.config_path),
            "status": self.status,
            "command": self.command,
            "args": list(self.args),
        }
        if self.detail:
            d["detail"] = self.detail
        return d


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
        claude_dir = resolved_env.get("CLAUDE_CONFIG_DIR")
        if claude_dir:
            return Path(claude_dir) / ".claude.json"
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
        claude_dir = (
            Path(resolved_env["CLAUDE_CONFIG_DIR"])
            if "CLAUDE_CONFIG_DIR" in resolved_env
            else resolved_home / ".claude"
        )
        if cfg.exists() or claude_dir.is_dir():
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
    # If path is a symlink or exists, resolve it so symlinks in dotfiles aren't replaced
    target_path = path.resolve() if path.exists() else path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600
    if target_path.exists():
        try:
            mode = os.stat(target_path).st_mode & 0o777
        except OSError:
            pass
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
        tmp_path.replace(target_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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


def _strip_json_comments(text: str) -> str:
    """Strip // and /* */ comments from JSON string while preserving strings."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _load_json_c(raw: str) -> tuple[object, bool]:
    """Parse JSON or JSONC. Returns (loaded_object, had_comments)."""
    try:
        return json.loads(raw), False
    except json.JSONDecodeError:
        stripped = _strip_json_comments(raw)
        return json.loads(stripped), True


def _register_json_client(
    client: str, path: Path, command: str, *, dry_run: bool = False
) -> tuple[str, str]:
    desired = _desired_json_entry(client, command)
    had_comments = False
    raw = ""
    if not path.is_file():
        data: dict[str, object] = {}
    else:
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            data = {}
        else:
            loaded, had_comments = _load_json_c(raw)
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
            return "unchanged", ""
        servers[SERVER_NAME] = updated
        status = "updated"
    else:
        servers[SERVER_NAME] = desired
        status = "added"

    detail = ""
    if not dry_run:
        if had_comments and raw:
            bak = path.with_name(f"{path.name}.bak")
            _write_text_atomic(bak, raw + "\n")
            detail = f"주석 포함 파일 — 원본 백업({bak.name}) 생성됨"
        _write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return status, detail


def _unregister_json_client(
    path: Path, *, matching_command: str | None = None, dry_run: bool = False
) -> tuple[str, str]:
    if not path.is_file():
        return "noop", ""
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        return "noop", ""
    loaded, had_comments = _load_json_c(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: JSON 최상위 구조가 객체(dict)가 아닙니다")
    servers = loaded.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return "noop", ""

    entry = servers[SERVER_NAME]
    if matching_command is not None:
        entry_cmd = entry.get("command") if isinstance(entry, dict) else None
        if not _is_same_command(entry_cmd, matching_command):
            return "noop", ""

    del servers[SERVER_NAME]
    detail = ""
    if not dry_run:
        if had_comments and raw:
            bak = path.with_name(f"{path.name}.bak")
            _write_text_atomic(bak, raw + "\n")
            detail = f"주석 포함 파일 — 원본 백업({bak.name}) 생성됨"
        _write_text_atomic(
            path, json.dumps(loaded, ensure_ascii=False, indent=2) + "\n"
        )
    return "removed", detail


def _toml_string(val: str) -> str:
    """Serialize a string as a TOML-safe literal UTF-8 string with escaped controls."""
    return json.dumps(val, ensure_ascii=False)


def _toml_key(k: str) -> str:
    """Format a TOML table/key name. Uses bare key if valid, else quotes it."""
    if re.fullmatch(r"[A-Za-z0-9_-]+", k):
        return k
    return json.dumps(k, ensure_ascii=False)


def _toml_format_value(v: object) -> str:
    """Recursively format a value for TOML inline serialization, raising ValueError on unsupported types."""
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        pairs = [
            f"{_toml_key(sub_k)} = {_toml_format_value(sub_v)}"
            for sub_k, sub_v in v.items()
        ]
        return f"{{ {', '.join(pairs)} }}"
    if isinstance(v, list):
        items = [_toml_format_value(item) for item in v]
        return f"[{', '.join(items)}]"
    raise ValueError(f"TOML로 직렬화할 수 없는 값 형식입니다: {type(v).__name__} ({v!r})")


def _codex_section_text(command: str, extra: dict[str, object] | None = None) -> str:
    lines = [
        f"[mcp_servers.{SERVER_NAME}]",
        f"command = {_toml_string(command)}",
        'args = ["mcp"]',
    ]
    if extra:
        for k, v in extra.items():
            if k in ("command", "args"):
                continue
            formatted_val = _toml_format_value(v)
            lines.append(f"{_toml_key(k)} = {formatted_val}")
    return "\n".join(lines) + "\n"


_MCP_SEG = r'(?:mcp_servers|"mcp_servers"|\'mcp_servers\')'
_HWP_SEG = r'(?:hwp-agent|"hwp-agent"|\'hwp-agent\')'
_DOT = r"[ \t]*\.[ \t]*"

_CODEX_MAIN_TABLE_RE = re.compile(
    rf"(?m)^\[[ \t]*{_MCP_SEG}{_DOT}{_HWP_SEG}[ \t]*\][ \t]*(?:#[^\r\n]*)?\r?$"
)
_CODEX_SECTION_HEADER_RE = re.compile(
    rf"(?m)^\[[ \t]*{_MCP_SEG}{_DOT}{_HWP_SEG}(?:{_DOT}[^\]]+)?[ \t]*\][ \t]*(?:#[^\r\n]*)?\r?\n"
)
_CODEX_GLOBAL_DOTTED_RE = re.compile(
    rf"(?m)^[ \t]*{_MCP_SEG}{_DOT}{_HWP_SEG}"
    rf"(?:{_DOT}[^=\r\n]+)*[ \t]*=.*$\r?\n?"
)
_CODEX_MCP_TABLE_RE = re.compile(
    rf"(?m)^\[[ \t]*{_MCP_SEG}[ \t]*\][ \t]*(?:#[^\r\n]*)?\r?$"
)
_CODEX_SCOPED_DOTTED_RE = re.compile(
    rf"(?m)^[ \t]*{_HWP_SEG}(?:{_DOT}[^=\r\n]+)+[ \t]*=.*$\r?\n?"
)


def _search_inline_table(text: str) -> tuple[int, int, str, str, str] | None:
    """Find the first inline table definition for hwp-agent in text.

    Returns (line_start, line_end, prefix, inner, suffix) where:
      - text[line_start:line_end] is the whole line
      - prefix is e.g. 'hwp-agent = {'
      - inner is the content between '{' and the matching '}'
      - suffix is '}' + optional trailing whitespace, comment, and newline.
    """
    pattern = re.compile(rf"(?m)^([ \t]*{_HWP_SEG}[ \t]*=[ \t]*\{{)")
    m = pattern.search(text)
    if not m:
        return None

    line_start = m.start()
    open_brace_end = m.end()
    prefix = m.group(1)

    depth = 0
    in_quote = None
    escape = False
    close_brace_idx = None

    for idx in range(open_brace_end, len(text)):
        ch = text[idx]
        if ch == "\n":
            break
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_quote:
                in_quote = None
            continue
        if ch in ('"', "'"):
            in_quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                close_brace_idx = idx
                break
            depth -= 1

    if close_brace_idx is None:
        return None

    eol = text.find("\n", close_brace_idx)
    line_end = eol + 1 if eol != -1 else len(text)
    trailing = text[close_brace_idx + 1 : line_end]
    if not re.match(r"^[ \t]*(?:#[^\r\n]*)?\r?\n?$", trailing):
        return None

    inner = text[open_brace_end:close_brace_idx]
    suffix = text[close_brace_idx:line_end]
    return (line_start, line_end, prefix, inner, suffix)


def _clean_codex_mcp_servers_table(raw: str) -> str:
    """Remove scoped dotted keys and inline table entries for hwp-agent ONLY within [mcp_servers]."""
    cleaned = _CODEX_GLOBAL_DOTTED_RE.sub("", raw)
    m = _CODEX_MCP_TABLE_RE.search(cleaned)
    if m:
        header_end = m.end()
        next_m = re.search(r"(?m)^\[", cleaned[header_end:])
        sec_end = header_end + next_m.start() if next_m else len(cleaned)
        sec_body = cleaned[header_end:sec_end]
        cleaned_sec = _CODEX_SCOPED_DOTTED_RE.sub("", sec_body)
        while True:
            inline_info = _search_inline_table(cleaned_sec)
            if not inline_info:
                break
            s, e, _, _, _ = inline_info
            cleaned_sec = cleaned_sec[:s] + cleaned_sec[e:]
        cleaned = cleaned[:header_end] + cleaned_sec + cleaned[sec_end:]
    return cleaned


def _remove_codex_sections(raw: str) -> str:
    """Remove [mcp_servers.hwp-agent] sections while preserving comments that precede next section."""
    while True:
        m = _CODEX_SECTION_HEADER_RE.search(raw)
        if not m:
            break
        start = m.start()
        header_end = m.end()
        next_m = re.search(r"(?m)^\[", raw[header_end:])
        if next_m:
            sec_end = header_end + next_m.start()
            sec_chunk = raw[header_end:sec_end]
            lines = sec_chunk.splitlines(keepends=True)
            last_content_idx = -1
            for idx in range(len(lines) - 1, -1, -1):
                s = lines[idx].strip()
                if s and not s.startswith("#"):
                    last_content_idx = idx
                    break
            if last_content_idx != -1:
                del_len = sum(len(lines[i]) for i in range(last_content_idx + 1))
                del_end = header_end + del_len
            else:
                del_end = sec_end
        else:
            del_end = len(raw)
        raw = raw[:start] + raw[del_end:]
    return raw


def _clean_codex_dotted_keys(raw: str) -> str:
    return _clean_codex_mcp_servers_table(raw)


def _find_top_level_inline_key_span(inner: str, key: str) -> tuple[int, int] | None:
    """Find (start, end) span of 'key = <val>' at the top level (depth 0) of an inline table."""
    i = 0
    n = len(inner)
    brace_depth = 0
    bracket_depth = 0
    in_quote = None
    escape = False

    while i < n:
        ch = inner[i]
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_quote:
                in_quote = None
            i += 1
            continue

        if ch in ('"', "'"):
            in_quote = ch
            i += 1
            continue

        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        elif ch == "}":
            brace_depth -= 1
            i += 1
            continue
        elif ch == "[":
            bracket_depth += 1
            i += 1
            continue
        elif ch == "]":
            bracket_depth -= 1
            i += 1
            continue

        if brace_depth == 0 and bracket_depth == 0:
            if inner[i : i + len(key)] == key:
                prev_char = inner[i - 1] if i > 0 else " "
                after_char = inner[i + len(key)] if i + len(key) < n else " "
                if not (prev_char.isalnum() or prev_char in "_-") and not (
                    after_char.isalnum() or after_char in "_-"
                ):
                    j = i + len(key)
                    while j < n and inner[j] in " \t":
                        j += 1
                    if j < n and inner[j] == "=":
                        val_start = j + 1
                        v = val_start
                        v_brace = 0
                        v_bracket = 0
                        v_quote = None
                        v_esc = False
                        while v < n:
                            vch = inner[v]
                            if v_quote:
                                if v_esc:
                                    v_esc = False
                                elif vch == "\\":
                                    v_esc = True
                                elif vch == v_quote:
                                    v_quote = None
                                v += 1
                                continue
                            if vch in ('"', "'"):
                                v_quote = vch
                                v += 1
                                continue
                            if vch == "{":
                                v_brace += 1
                            elif vch == "}":
                                v_brace -= 1
                            elif vch == "[":
                                v_bracket += 1
                            elif vch == "]":
                                v_bracket -= 1
                            elif v_brace == 0 and v_bracket == 0:
                                if vch == ",":
                                    break
                            v += 1
                        return (i, v)
        i += 1
    return None


def _update_inline_table(line: str, command: str) -> str:
    cmd_str = f"command = {_toml_string(command)}"
    args_str = 'args = ["mcp"]'
    inline_info = _search_inline_table(line)
    if not inline_info:
        return line
    _, _, prefix, inner, suffix = inline_info

    cmd_span = _find_top_level_inline_key_span(inner, "command")
    if cmd_span is not None:
        inner = inner[: cmd_span[0]] + cmd_str + inner[cmd_span[1] :]
    else:
        inner = f" {cmd_str}," + inner

    args_span = _find_top_level_inline_key_span(inner, "args")
    if args_span is not None:
        inner = inner[: args_span[0]] + args_str + inner[args_span[1] :]
    else:
        inner = inner.rstrip() + f", {args_str} "

    return f"{prefix}{inner}{suffix}"


def _replace_top_level_section_key(
    sec_body: str, key: str, replacement: str
) -> tuple[str, bool]:
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=")
    m = pattern.search(sec_body)
    if not m:
        return sec_body, False

    key_line_start = m.start()
    eq_pos = m.end() - 1
    n = len(sec_body)

    # Find start of value after '='
    val_start = eq_pos + 1
    while val_start < n and sec_body[val_start] in " \t":
        val_start += 1

    if val_start < n and sec_body[val_start] == "[":
        # Array (can be multiline)
        i = val_start + 1
        bracket_depth = 1
        in_quote = None
        escape = False
        while i < n and bracket_depth > 0:
            ch = sec_body[i]
            if in_quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_quote:
                    in_quote = None
            elif ch in ('"', "'"):
                in_quote = ch
            elif ch == "#":
                # comment inside array: ignore until end of line
                while i < n and sec_body[i] not in "\r\n":
                    i += 1
                continue
            elif ch == "[":
                bracket_depth += 1
            elif ch == "]":
                bracket_depth -= 1
            i += 1
        # Consume trailing whitespace/comment until end of line
        while i < n and sec_body[i] not in "\r\n":
            i += 1
        if i < n and sec_body[i] == "\r":
            i += 1
        if i < n and sec_body[i] == "\n":
            i += 1
        end = i
    elif val_start < n and sec_body[val_start] == "{":
        # Inline table
        i = val_start + 1
        brace_depth = 1
        in_quote = None
        escape = False
        while i < n and brace_depth > 0:
            ch = sec_body[i]
            if in_quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_quote:
                    in_quote = None
            elif ch in ('"', "'"):
                in_quote = ch
            elif ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
            i += 1
        while i < n and sec_body[i] not in "\r\n":
            i += 1
        if i < n and sec_body[i] == "\r":
            i += 1
        if i < n and sec_body[i] == "\n":
            i += 1
        end = i
    else:
        # Single line or simple value
        eol = sec_body.find("\n", eq_pos)
        end = eol + 1 if eol != -1 else n

    new_sec = sec_body[:key_line_start] + replacement + "\n" + sec_body[end:]
    return new_sec, True


def _update_codex_main_section(
    raw: str, command: str, extra: dict[str, object] | None = None
) -> str:
    match = _CODEX_MAIN_TABLE_RE.search(raw)
    if match:
        header_end = match.end()
        next_match = re.search(r"(?m)^\[", raw[header_end:])
        sec_end = header_end + next_match.start() if next_match else len(raw)

        sec_body = raw[header_end:sec_end]
        cmd_str = f"command = {_toml_string(command)}"
        args_str = 'args = ["mcp"]'

        sec_body, cmd_replaced = _replace_top_level_section_key(
            sec_body, "command", cmd_str
        )
        if not cmd_replaced:
            sec_body = f"\n{cmd_str}\n" + sec_body

        sec_body, args_replaced = _replace_top_level_section_key(
            sec_body, "args", args_str
        )
        if not args_replaced:
            if cmd_str in sec_body:
                sec_body = sec_body.replace(cmd_str, f"{cmd_str}\n{args_str}", 1)
            else:
                sec_body = f"\n{args_str}\n" + sec_body

        return raw[:header_end] + sec_body + raw[sec_end:]

    m = _CODEX_MCP_TABLE_RE.search(raw)
    if m:
        header_end = m.end()
        next_m = re.search(r"(?m)^\[", raw[header_end:])
        sec_end = header_end + next_m.start() if next_m else len(raw)
        sec_body = raw[header_end:sec_end]
        inline_info = _search_inline_table(sec_body)
        if inline_info:
            s, e, _, _, _ = inline_info
            updated_line = _update_inline_table(sec_body[s:e], command)
            updated_sec = sec_body[:s] + updated_line + sec_body[e:]
            return raw[:header_end] + updated_sec + raw[sec_end:]

    cleaned = _clean_codex_mcp_servers_table(raw)
    sep = "" if cleaned.endswith("\n\n") else ("\n" if cleaned.endswith("\n") else "\n\n")
    return cleaned + sep + _codex_section_text(command, extra=extra)


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
    rebuilt = _update_codex_main_section(
        raw, command, extra=existing if isinstance(existing, dict) else None
    )

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

    rebuilt = _remove_codex_sections(raw)
    rebuilt = _clean_codex_mcp_servers_table(rebuilt)
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
        try:
            if client == "codex":
                status = _register_codex_toml(cfg, resolved_cmd, dry_run=dry_run)
                detail = ""
            else:
                status, detail = _register_json_client(
                    client, cfg, resolved_cmd, dry_run=dry_run
                )
        except Exception as exc:
            status = "error"
            detail = str(exc)
        actions.append(
            RegistrationAction(
                client=client,
                label=CLIENT_LABELS[client],
                config_path=cfg,
                status=status,
                command=resolved_cmd,
                detail=detail,
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
        try:
            if client == "codex":
                status = _unregister_codex_toml(
                    cfg, matching_command=resolved_matching, dry_run=dry_run
                )
                detail = ""
            else:
                status, detail = _unregister_json_client(
                    cfg, matching_command=resolved_matching, dry_run=dry_run
                )
        except Exception as exc:
            status = "error"
            detail = str(exc)
        actions.append(
            RegistrationAction(
                client=client,
                label=CLIENT_LABELS[client],
                config_path=cfg,
                status=status,
                detail=detail,
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
        if act.status == "error" and act.detail:
            lines.append(f"  - {act.label}: {status_ko} — {act.detail} ({act.config_path})")
        else:
            lines.append(f"  - {act.label}: {status_ko} ({act.config_path})")

    lines.append("")
    lines.append("[이렇게 쓰세요]")
    for act in actions:
        if act.status == "error":
            continue
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
        if act.status == "error" and act.detail:
            lines.append(f"  - {act.label}: {status_ko} — {act.detail} ({act.config_path})")
        else:
            lines.append(f"  - {act.label}: {status_ko} ({act.config_path})")
    return "\n".join(lines)
