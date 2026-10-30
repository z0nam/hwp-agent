"""Guards for the Windows installer script (installer/hwp-agent.iss) — #85.

The installer's {app} is %LOCALAPPDATA%\\hwp-agent, the same folder jaix and
``hwp-agent setup`` fill with the hwp2hwpx jar and rhwp. Uninstalling the
standalone build must leave those, and must not take jaix's MCP entries with it.
Inno only runs on Windows (the windows-exe smoke test exercises it there); these
checks read the script so a careless edit fails on every OS.
"""

from __future__ import annotations

import re
from pathlib import Path

ISS_PATH = Path(__file__).resolve().parents[1] / "installer" / "hwp-agent.iss"
ISS = ISS_PATH.read_text(encoding="utf-8")


def _section(name: str) -> str:
    m = re.search(rf"^\[{name}\]\s*$(.*?)(?=^\[[A-Za-z]+\]\s*$|\Z)", ISS, re.M | re.S)
    return m.group(1) if m else ""


def test_uninstall_never_wipes_the_shared_folder():
    # jaix's jar/rhwp live in {app}: no recursive delete of it, in any form
    assert "filesandordirs" not in _section("UninstallDelete")
    assert not re.search(r"rmdir\s+/s|rd\s+/s|DelTree\s*\(|Remove-Item[^\n]*-Recurse", ISS, re.I)
    # the only files installed are the exe and the private JRE
    files = _section("Files")
    assert re.findall(r'DestDir:\s*"([^"]+)"', files) == ["{app}", "{app}\\jre"]


def test_uninstall_removes_only_its_own_mcp_entries():
    run = _section("UninstallRun")
    assert "--unregister all" in run and '--matching-command ""{app}\\hwp-agent.exe""' in run


def test_jaix_pc_unchecks_claude_code_and_codex():
    assert "function IsJaixInstalled" in ISS
    assert "{localappdata}\\jaix" in ISS and "jaix.exe" in ISS
    assert "AiClientPage.Values[1] := IsClaudeCodeInstalled() and not Jaix;" in ISS
    assert "AiClientPage.Values[2] := IsCodexInstalled() and not Jaix;" in ISS
    assert "jaix가 설치된 PC라 Claude Code·Codex 체크를 껐습니다" in ISS  # the one-line reason


def test_uninstall_stops_only_this_exe_and_cleans_path():
    code = _section("Code")
    stop = code[code.index("procedure StopOwnProcesses"):code.index("procedure RemoveFromUserPath")]
    # matched by full path, so jaix's uvx-launched hwp-agent.exe keeps running
    assert "ExecutablePath -eq" in stop and "{app}\\hwp-agent.exe" in stop
    assert "taskkill" not in ISS.lower()  # by image name would hit jaix's too
    step = code[code.index("procedure CurUninstallStepChanged"):]
    assert "usUninstall" in step and "StopOwnProcesses" in step
    assert "RemoveFromUserPath(AppDir)" in step
    # locked exe: deleted at next logon; the folder only if empty
    assert "RunOnce" in step and 'del /f /q "\' + Exe' in step and 'rmdir "\' + AppDir' in step
