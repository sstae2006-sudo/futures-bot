<#
.SYNOPSIS
  Cleanly shuts down every process futures-bot's start.ps1 started --
  backend and frontend -- by freeing their ports, regardless of how
  they were originally launched. See BOOT_CHECKLIST.md.

.DESCRIPTION
  By-port process lookup is authoritative here, not the PID files
  start.ps1 saves (those are informational only -- see _common.ps1).
  "Nothing was running" on either port is a valid success, not an
  error.
#>

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$RepoRoot = Assert-RepoRoot
Write-Host "=== Stopping futures-bot ===" -ForegroundColor Cyan

foreach ($name in @("backend", "frontend")) {
    Remove-ProcessInfo -RepoRoot $RepoRoot -Name $name
}

Write-Step "Stopping backend (port $BackendPort)"
Stop-ProcessOnPort -Port $BackendPort -Label "backend"
$backendFree = Wait-ForPortFree -Port $BackendPort -TimeoutSeconds 10

Write-Step "Stopping frontend (port $FrontendPort)"
Stop-ProcessOnPort -Port $FrontendPort -Label "frontend"
$frontendFree = Wait-ForPortFree -Port $FrontendPort -TimeoutSeconds 10

Write-Host ""
if ($backendFree -and $frontendFree) {
    Write-Host "Everything stopped. Ports $BackendPort and $FrontendPort are free." -ForegroundColor Green
    exit 0
}

if (-not $backendFree) {
    Write-Host "WARNING: port $BackendPort is still in use." -ForegroundColor Yellow
}
if (-not $frontendFree) {
    Write-Host "WARNING: port $FrontendPort is still in use." -ForegroundColor Yellow
}
Write-Host "Run scripts\status.ps1 for detail, or find/close the process manually:" -ForegroundColor Yellow
Write-Host "  Get-NetTCPConnection -LocalPort <port> | Select OwningProcess" -ForegroundColor Yellow
exit 1
