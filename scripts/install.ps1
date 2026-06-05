# install.ps1 — Windows setup: install the hwp-agent CLI, register the Claude Code
# skill, and download the converter jar. PowerShell counterpart of install.sh.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# What it does:
#   1. installs the `hwp-agent` CLI on PATH (uv or pipx, editable),
#   2. COPIES the hwp-agent skill into %USERPROFILE%\.claude\skills\ (Windows
#      symlinks need Developer Mode, so we copy),
#   3. runs `hwp-agent setup` to fetch the prebuilt converter jar (needs a JRE 17+
#      only to *run* it — no JDK/Maven).
#
# Skip the jar download (HWPX-only) with:  $env:SKIP_JAR=1; .\scripts\install.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$SkillsDir = if ($env:CLAUDE_SKILLS_DIR) { $env:CLAUDE_SKILLS_DIR } `
             else { Join-Path $env:USERPROFILE ".claude\skills" }

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "warn: $m" -ForegroundColor Yellow }

# --- 1. Install the CLI on PATH --------------------------------------------
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Info "Installing hwp-agent CLI with uv (editable)..."
    uv tool install --editable "$RepoRoot" --force
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    Info "Installing hwp-agent CLI with pipx (editable)..."
    pipx install --editable "$RepoRoot" --force
} else {
    throw "need 'uv' or 'pipx' to install the CLI on PATH. Install uv: https://docs.astral.sh/uv/  or:  pip install -e `"$RepoRoot`""
}

if (Get-Command hwp-agent -ErrorAction SilentlyContinue) {
    Info "CLI ready: $(hwp-agent --version)"
} else {
    Warn "hwp-agent not on PATH yet — open a new shell, or add the installer's bin dir to PATH."
}

# --- 2. Register the Claude Code skill (copy, not symlink) ------------------
Info "Installing hwp-agent skill into $SkillsDir ..."
$dest = Join-Path $SkillsDir "hwp-agent"
New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Copy-Item -Recurse -Force (Join-Path $RepoRoot "skills\hwp-agent") $dest
Info "Skill copied: $dest"

# --- 3. Converter jar -------------------------------------------------------
if ($env:SKIP_JAR) {
    Info "SKIP_JAR set — skipping converter download (HWP->HWPX convert won't work)."
} elseif (Get-Command hwp-agent -ErrorAction SilentlyContinue) {
    Info "Downloading converter jar (hwp-agent setup)..."
    try { hwp-agent setup } catch { Warn "jar download failed: $_  (run 'hwp-agent setup' later)" }
} else {
    Warn "Run 'hwp-agent setup' once the CLI is on PATH to download the converter jar."
}

Write-Host ""
Write-Host "Done. Restart Claude Code and type '/' — you should see /hwp-agent." -ForegroundColor Green
Write-Host "Try:  hwp-agent classify <file.hwpx>"
