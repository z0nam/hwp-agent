# render-inbox.ps1 -- hwp-agent Tier-2 worker action (runs on namun-ji, session 1).
#
# Triggered by the "hwp-agent-hwp2pdf" scheduled task (schtasks /run, interactive
# /it -> session 1, where Hancom COM works). For each <job>.hwp/.hwpx in the inbox
# it runs hwp2pdf (PDF + DOCX), moves the products to the outbox, and drops a
# <job>.done marker (or <job>.err on failure). One file at a time.
#
# BUSY GUARD (issue #17): this worker MUST NOT close a Hangul window the user is
# editing. It never passes --kill-hwp. Before rendering it checks for a running
# Hangul process; if one exists it drops a <job>.busy marker and skips the job
# (the client then falls back to the local rhwp engine for PDF). hwp2pdf spawns
# and quits its own Hangul instance for the conversion, so we only render when no
# Hangul is already open. Tradeoff: a stale/orphaned Hangul process also blocks
# rendering (looks "busy") until it is closed on the node -- safer than killing
# a live document.
#
# NOTE: the Hangul process name is assumed to be "Hwp" (Hancom Office 2018+).
# Confirm on the node with `Get-Process | ? { $_.Name -like "*hwp*" }` and set
# -HwpProcName if it differs.
#
# ASCII-only on purpose (survives CP949 PowerShell 5). Do not add non-ASCII text.

param(
  [string]$ExePath     = "$env:USERPROFILE\dev\hwp2pdf\.venv\Scripts\hwp2pdf.exe",
  [string]$Inbox       = "$env:USERPROFILE\.hwp-agent\inbox",
  [string]$Outbox      = "$env:USERPROFILE\.hwp-agent\outbox",
  [string]$HwpProcName = "Hwp"
)

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force -Path $Inbox, $Outbox | Out-Null

$files = Get-ChildItem -Path $Inbox -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Extension -eq ".hwp" -or $_.Extension -eq ".hwpx" }
if (-not $files) { exit 0 }

foreach ($f in $files) {
  $job = $f.BaseName

  # Busy guard: never touch a Hangul window the user is editing.
  if (Get-Process -Name $HwpProcName -ErrorAction SilentlyContinue) {
    New-Item -ItemType File -Force -Path (Join-Path $Outbox "$job.busy") | Out-Null
    continue
  }

  $log = & $ExePath $f.FullName --pdf --docx 2>&1 | Out-String
  $rc = $LASTEXITCODE

  # hwp2pdf writes outputs BESIDE the source (i.e. in the inbox) as <job>.pdf/.docx
  $moved = $false
  foreach ($ext in @("pdf", "docx")) {
    $prod = Join-Path $Inbox "$job.$ext"
    if (Test-Path $prod) {
      Move-Item -Force $prod (Join-Path $Outbox "$job.$ext")
      $moved = $true
    }
  }

  if ($rc -eq 0 -and $moved) {
    New-Item -ItemType File -Force -Path (Join-Path $Outbox "$job.done") | Out-Null
  } else {
    $tail = ($log -split "`r?`n" | Select-Object -Last 8) -join "`n"
    Set-Content -Path (Join-Path $Outbox "$job.err") -Value "exit=$rc`n$tail"
  }

  Remove-Item -Force $f.FullName -ErrorAction SilentlyContinue
}
