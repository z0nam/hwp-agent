# render-inbox.ps1 -- hwp-agent Tier-2 worker action (runs on namun-ji, session 1).
#
# Triggered by the "hwp-agent-hwp2pdf" scheduled task (schtasks /run, interactive
# /it -> session 1, where Hancom COM works). For each <job>.hwp/.hwpx in the inbox
# it runs hwp2pdf (PDF + DOCX), moves the products to the outbox, and drops a
# <job>.done marker (or <job>.err on failure). One file at a time; Hancom is quit
# by hwp2pdf's own lifecycle guard (--kill-hwp clears any stale instance first).
#
# ASCII-only on purpose (survives CP949 PowerShell 5). Do not add non-ASCII text.

param(
  [string]$ExePath = "$env:USERPROFILE\dev\hwp2pdf\.venv\Scripts\hwp2pdf.exe",
  [string]$Inbox   = "$env:USERPROFILE\.hwp-agent\inbox",
  [string]$Outbox  = "$env:USERPROFILE\.hwp-agent\outbox"
)

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force -Path $Inbox, $Outbox | Out-Null

$files = Get-ChildItem -Path $Inbox -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Extension -eq ".hwp" -or $_.Extension -eq ".hwpx" }
if (-not $files) { exit 0 }

foreach ($f in $files) {
  $job = $f.BaseName
  $log = & $ExePath $f.FullName --pdf --docx --kill-hwp 2>&1 | Out-String
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
