<#
.SYNOPSIS
  Read-only status report: backend/frontend running?, database found?,
  ports in use?, Python venv active?, API/frontend reachable? Never
  starts or stops anything.
#>

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_common.ps1"

$RepoRoot = Assert-RepoRoot
Write-Host "=== futures-bot status ===" -ForegroundColor Cyan
Write-Host "Repository: $RepoRoot"
Write-Host ""

# Python venv: "present on disk" and "active in this shell" are two
# different, separately-labeled facts -- a fresh, unactivated terminal
# is the normal case for status.ps1 and shouldn't read as a failure.
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    Write-Host "Python venv (on disk): present ($VenvPython)" -ForegroundColor Green
} else {
    Write-Host "Python venv (on disk): MISSING -- run: python -m venv .venv" -ForegroundColor Red
}
if ($env:VIRTUAL_ENV) {
    Write-Host "Python venv (this shell): active ($($env:VIRTUAL_ENV))" -ForegroundColor Green
} else {
    Write-Host "Python venv (this shell): not active (informational only -- start.ps1 always uses the venv's python directly, regardless of this)" -ForegroundColor DarkGray
}
Write-Host ""

# Backend: "running" (port in use) vs "reachable" (HTTP 200) are
# reported separately -- they can legitimately disagree while the
# process is still booting.
$backendPids = Get-ProcessIdsOnPort -Port $BackendPort
if ($backendPids.Count -gt 0) {
    Write-Host "Backend running:    YES (PID $($backendPids -join ', ') on port $BackendPort)" -ForegroundColor Green
} else {
    Write-Host "Backend running:    NO (port $BackendPort free)" -ForegroundColor Red
}
$apiOk = Test-HttpOk -Url "$BackendUrl$HealthPath"
if ($apiOk) {
    Write-Host "API reachable:      YES ($BackendUrl$HealthPath -> 200)" -ForegroundColor Green
} else {
    Write-Host "API reachable:      NO" -ForegroundColor Red
}
Write-Host ""

# Frontend: same running-vs-reachable split.
$frontendPids = Get-ProcessIdsOnPort -Port $FrontendPort
if ($frontendPids.Count -gt 0) {
    Write-Host "Frontend running:   YES (PID $($frontendPids -join ', ') on port $FrontendPort)" -ForegroundColor Green
} else {
    Write-Host "Frontend running:   NO (port $FrontendPort free)" -ForegroundColor Red
}
$feOk = Test-HttpOk -Url $FrontendUrl
if ($feOk) {
    Write-Host "Frontend reachable: YES ($FrontendUrl -> 200)" -ForegroundColor Green
} else {
    Write-Host "Frontend reachable: NO" -ForegroundColor Red
}
Write-Host ""

# Database.
$MarketDataDb = Join-Path $RepoRoot "market_data.db"
if (Test-Path $MarketDataDb) {
    $sizeMb = [math]::Round((Get-Item $MarketDataDb).Length / 1MB, 1)
    Write-Host "market_data.db:     found ($sizeMb MB)" -ForegroundColor Green
} else {
    Write-Host "market_data.db:     MISSING" -ForegroundColor Red
}
$ResearchDb = Join-Path $RepoRoot "research.db"
if (Test-Path $ResearchDb) {
    Write-Host "research.db:        found (FYI -- auto-creates itself on first API boot if missing)" -ForegroundColor Green
} else {
    Write-Host "research.db:        not found yet (FYI only -- auto-creates on first API boot)" -ForegroundColor DarkGray
}

Write-Host ""
if ($backendPids.Count -gt 0 -and $frontendPids.Count -gt 0 -and $apiOk -and $feOk) {
    Write-Host "Everything is running normally." -ForegroundColor Green
} else {
    Write-Host "Something isn't running or reachable. Run scripts\start.ps1 to (re)start everything." -ForegroundColor Yellow
}
