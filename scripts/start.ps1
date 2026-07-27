<#
.SYNOPSIS
  The ONE command to boot futures-bot after a reboot: verifies the
  repo/venv/dependencies/database, frees the backend+frontend ports of
  any stale processes, starts both, waits until each actually
  responds, opens the browser, and prints a summary. See
  BOOT_CHECKLIST.md and docs/DATABASE_VALIDATION.md.

.DESCRIPTION
  Any failure stops immediately with a red "FAILED: ..." + yellow
  "How to fix: ..." message (see _common.ps1's Write-Failure) --
  never a silent continue, never a raw stack trace.
#>

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

Write-Host "=== futures-bot startup ===" -ForegroundColor Cyan
Write-Host ""

# 1. Verify repository
Write-Step "Locating repository"
$RepoRoot = Assert-RepoRoot
Set-Location $RepoRoot
Write-Ok "Repository root: $RepoRoot"

# 2. Activate Python virtual environment
Write-Step "Activating Python virtual environment"
$Venv = Assert-Venv -RepoRoot $RepoRoot
try {
    . $Venv.Activate
} catch {
    Write-Failure `
        "Could not activate the virtual environment: $($_.Exception.Message)" `
        "This is usually a PowerShell execution-policy restriction. Try: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   then re-run this script."
}
Write-Ok "Virtual environment active ($($Venv.Python))"

# 3. Ensure Python packages are installed AND up to date -- every run,
#    not just when something looks missing, so a new dependency never
#    requires the user to remember to reinstall by hand.
Write-Step "Installing/updating Python dependencies (pip install -e .)"
& $Venv.Python -m pip install -e $RepoRoot --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Failure `
        "pip install -e . failed (exit code $LASTEXITCODE)" `
        "Run manually to see the full error: $($Venv.Python) -m pip install -e `"$RepoRoot`""
}
Write-Ok "Python dependencies up to date"

# 4. Ensure npm dependencies are installed AND up to date -- same
#    reasoning as step 3.
Write-Step "Installing/updating npm dependencies"
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-Failure `
        "npm.cmd not found on PATH" `
        "Install Node.js from https://nodejs.org and make sure npm is on PATH, then re-run this script."
}
$FrontendDir = Join-Path $RepoRoot "frontend"
Push-Location $FrontendDir
& $npmCmd.Source install --no-fund --no-audit | Out-Null
$npmExit = $LASTEXITCODE
Pop-Location
if ($npmExit -ne 0) {
    Write-Failure `
        "npm install failed in frontend\ (exit code $npmExit)" `
        "Run manually to see the full error: cd frontend; npm install"
}
Write-Ok "npm dependencies up to date"

# 5. Verify the database exists (warning only -- the app still boots
#    and runs without it; market-data features are just empty until
#    it's populated. research.db is not checked here: it auto-creates
#    itself on first API boot via TradeStore.ensure_schema()).
Write-Step "Checking market_data.db"
$MarketDataDb = Join-Path $RepoRoot "market_data.db"
$DbFound = Test-Path $MarketDataDb
if ($DbFound) {
    $sizeMb = [math]::Round((Get-Item $MarketDataDb).Length / 1MB, 1)
    Write-Ok "market_data.db found ($sizeMb MB)"
} else {
    Write-WarnLine "market_data.db not found -- the app will still start, but market-data features will be empty. See tools/pull_massive_flatfiles.py to populate it."
}

# 6. Kill any old backend/frontend processes already using our ports.
Write-Step "Freeing ports $BackendPort (backend) / $FrontendPort (frontend) of any old processes"
Stop-ProcessOnPort -Port $BackendPort -Label "backend"
Stop-ProcessOnPort -Port $FrontendPort -Label "frontend"
if (-not (Wait-ForPortFree -Port $BackendPort -TimeoutSeconds 10)) {
    Write-Failure `
        "Port $BackendPort is still in use after attempting to stop it" `
        "Find what's using it: Get-NetTCPConnection -LocalPort $BackendPort | Select OwningProcess   then Get-Process -Id <pid> and close it manually (may need an elevated PowerShell)."
}
if (-not (Wait-ForPortFree -Port $FrontendPort -TimeoutSeconds 10)) {
    Write-Failure `
        "Port $FrontendPort is still in use after attempting to stop it" `
        "Find what's using it: Get-NetTCPConnection -LocalPort $FrontendPort | Select OwningProcess   then Get-Process -Id <pid> and close it manually. Note: the frontend's own 'npm run dev' also force-kills EVERY node.exe process on Windows as its first step (frontend/scripts/kill-vite.js, pre-existing) -- if you have other Node projects running, they'll be stopped too."
}
Write-Ok "Ports are free"

# 7. Start the FastAPI backend.
Write-Step "Starting FastAPI backend"
$StartupDir = Get-StartupDir -RepoRoot $RepoRoot
$BackendOutLog = Join-Path $StartupDir "backend.out.log"
$BackendErrLog = Join-Path $StartupDir "backend.err.log"
$backendProc = Start-Process -FilePath $Venv.Python -ArgumentList "-m", "futures_bot.api" `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $BackendOutLog -RedirectStandardError $BackendErrLog
Save-ProcessInfo -RepoRoot $RepoRoot -Name "backend" -ProcessId $backendProc.Id
Write-Ok "Backend process started (PID $($backendProc.Id))"

# 8. Wait until the backend actually responds.
Write-Step "Waiting for backend to respond at $BackendUrl"
if (-not (Wait-ForHttp -Url "$BackendUrl$HealthPath" -TimeoutSeconds 30)) {
    Write-Failure `
        "Backend did not respond within 30 seconds" `
        "Check the logs: Get-Content '$BackendOutLog' -Tail 50   and Get-Content '$BackendErrLog' -Tail 50 . Common causes: a missing dependency (re-run pip install -e .), the port still held by something step 6 couldn't stop, or a config.yaml error."
}
Write-Ok "Backend is responding"

# 9. Start the Vite frontend.
#
# Deliberately NOT "npm run dev": that script is
# `node scripts/kill-vite.js && vite`, and kill-vite.js's
# `taskkill /F /IM node.exe` matches (and kills) its own node.exe
# process by image name -- confirmed empirically, it exits nonzero
# every time, so `&& vite` never runs. That's a pre-existing bug in
# frontend/ this task was told not to touch. Calling the local vite
# binary directly sidesteps it entirely without changing any existing
# project file -- and kill-vite.js's job is redundant here anyway,
# since step 6 above already frees port 5173 before we get here.
#
# --host 127.0.0.1 is explicit on purpose: vite.config.ts doesn't set
# a host, so Vite falls back to binding "localhost" -- which Node
# resolves to the IPv6 loopback ([::1]) first on this machine, not
# 127.0.0.1. Confirmed empirically: the dev server came up fine but
# only accepted http://localhost:5173, not http://127.0.0.1:5173. The
# backend already binds explicit IPv4 (see api/__main__.py's
# --host 127.0.0.1 default); this makes the frontend consistent with
# it rather than depending on a machine's IPv4-vs-IPv6 DNS preference.
Write-Step "Starting Vite frontend"
$FrontendOutLog = Join-Path $StartupDir "frontend.out.log"
$FrontendErrLog = Join-Path $StartupDir "frontend.err.log"
$viteCmd = Join-Path $FrontendDir "node_modules\.bin\vite.cmd"
if (-not (Test-Path $viteCmd)) {
    Write-Failure `
        "frontend\node_modules\.bin\vite.cmd not found" `
        "npm install may not have completed. Run manually: cd frontend; npm install"
}
$frontendProc = Start-Process -FilePath $viteCmd -ArgumentList "--host", "127.0.0.1" `
    -WorkingDirectory $FrontendDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $FrontendOutLog -RedirectStandardError $FrontendErrLog
Save-ProcessInfo -RepoRoot $RepoRoot -Name "frontend" -ProcessId $frontendProc.Id
Write-Ok "Frontend launcher started (PID $($frontendProc.Id))"

# 10. Wait until it is responding.
Write-Step "Waiting for frontend to respond at $FrontendUrl"
if (-not (Wait-ForHttp -Url $FrontendUrl -TimeoutSeconds 30)) {
    Write-Failure `
        "Frontend did not respond within 30 seconds" `
        "Check the logs: Get-Content '$FrontendOutLog' -Tail 50   and Get-Content '$FrontendErrLog' -Tail 50 . Common causes: npm install didn't finish (re-run step 4 manually), or port $FrontendPort still held by something."
}
Write-Ok "Frontend is responding"

# 11. Automatically open the browser.
Write-Step "Opening browser"
Start-Process $FrontendUrl

# 12. Green success message.
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " futures-bot is running"                    -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host " Backend:   $BackendUrl"                    -ForegroundColor Green
Write-Host " Frontend:  $FrontendUrl"                    -ForegroundColor Green
if ($DbFound) {
    Write-Host " Database:  market_data.db found ($sizeMb MB)" -ForegroundColor Green
} else {
    Write-Host " Database:  market_data.db MISSING -- market-data features will be empty" -ForegroundColor Yellow
}
Write-Host " API:       responding (200 OK)"              -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Logs:    $StartupDir\{backend,frontend}.{out,err}.log" -ForegroundColor DarkGray
Write-Host "Status:  scripts\status.ps1     Stop: scripts\stop.ps1     Restart: scripts\restart.ps1" -ForegroundColor DarkGray
