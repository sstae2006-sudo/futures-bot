<#
.SYNOPSIS
  Stops everything, then starts everything again. Equivalent to
  running stop.ps1 then start.ps1, but propagates failure correctly.

.DESCRIPTION
  IMPORTANT: `exit N` inside a script invoked via `& "child.ps1"` (not
  dot-sourced) does NOT terminate the calling script -- $LASTEXITCODE
  is set, but this script would otherwise sail on into start.ps1 even
  if stop.ps1 failed to free the ports. Each call below is checked
  explicitly instead of relying on the child's own `exit`.
#>

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

Write-Host "=== Restarting futures-bot ===" -ForegroundColor Cyan

& "$PSScriptRoot\stop.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Failure `
        "stop.ps1 did not finish cleanly (exit code $LASTEXITCODE)" `
        "Run scripts\status.ps1 to see what's still holding a port, close it manually, then run scripts\start.ps1 directly."
}

& "$PSScriptRoot\start.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Failure `
        "start.ps1 did not finish cleanly (exit code $LASTEXITCODE)" `
        "Scroll up for the specific FAILED message from start.ps1 -- it explains exactly what went wrong and how to fix it."
}
