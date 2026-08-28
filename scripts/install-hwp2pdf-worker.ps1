# install-hwp2pdf-worker.ps1 -- one-time setup on namun-ji for hwp-agent Tier-2.
#
# Registers the session-1 worker that turns HWP/HWPX into PDF/DOCX via Hancom COM.
# Run ONCE on the Windows node (namun-ji), in the logged-in user's session:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-hwp2pdf-worker.ps1
#
# Idempotent (re-run safe). ASCII-only (survives CP949 PowerShell 5).
#
# PRECONDITIONS this script does NOT enforce (Hancom COM needs them, see
# docs/output-verification.md) -- verify manually:
#   1. Auto-login enabled AND the desktop stays unlocked (session 1 live).
#      Without it, COM hangs and every convert times out.
#   2. FilePathCheckerModule registered (done 2026-06-23):
#        HKCU\Software\HNC\HwpAutomation\Modules  value FilePathCheckerModule = <dll path>
#      Without it Hancom pops a file-access modal and COM waits forever.
#   3. hwp2pdf installed at -ExePath (default: %USERPROFILE%\dev\hwp2pdf\.venv\Scripts\hwp2pdf.exe).

param(
  [string]$ExePath  = "$env:USERPROFILE\dev\hwp2pdf\.venv\Scripts\hwp2pdf.exe",
  [string]$Base     = "$env:USERPROFILE\.hwp-agent",
  [string]$TaskName = "hwp-agent-hwp2pdf"
)

$ErrorActionPreference = "Stop"

$Inbox  = Join-Path $Base "inbox"
$Outbox = Join-Path $Base "outbox"
New-Item -ItemType Directory -Force -Path $Inbox, $Outbox | Out-Null

# install the worker action next to the queue
$worker = Join-Path $Base "render-inbox.ps1"
Copy-Item -Force (Join-Path $PSScriptRoot "render-inbox.ps1") $worker

# register the interactive (session-1) task; /it is the proven trigger
$tr = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$worker`" -ExePath `"$ExePath`" -Inbox `"$Inbox`" -Outbox `"$Outbox`""
schtasks /create /tn $TaskName /it /sc ONCE /st 00:00 /tr $tr /f | Out-Null

Write-Host "installed:"
Write-Host "  task    : $TaskName  (schtasks /run to trigger; /it = session 1)"
Write-Host "  worker  : $worker"
Write-Host "  inbox   : $Inbox"
Write-Host "  outbox  : $Outbox"
Write-Host "  exe     : $ExePath"
if (-not (Test-Path $ExePath)) {
  Write-Warning "hwp2pdf.exe not found at $ExePath -- fix -ExePath or build hwp2pdf first."
}
Write-Host ""
Write-Host "REMINDERS (COM requires these; this script cannot enforce them):"
Write-Host "  - keep this box auto-logged-in and the session UNLOCKED (session 1)"
Write-Host "  - FilePathCheckerModule must stay registered (HKCU HwpAutomation Modules)"
Write-Host ""
Write-Host "test:  schtasks /run /tn $TaskName   (after dropping a .hwpx in $Inbox)"
