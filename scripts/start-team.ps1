<#
.SYNOPSIS
  Team-deployment boot: one FastAPI process serving the built dashboard
  and talking to a shared TimescaleDB, reachable over Tailscale. See
  TEAM_DEPLOYMENT.md.

.DESCRIPTION
  NOT a replacement for start.ps1 -- that script stays the single-
  developer path (local SQLite, separate Vite dev server, loopback-
  only) completely unchanged. This script is the team-mode path:
  builds the frontend once (VITE_API_BASE_URL= npm run build, same
  convention deploy/DEPLOYMENT.md and Dockerfile.api already use for
  the VPS/Docker deploy), then starts the backend with
  --allow-network-exposure so it serves both the API and the built
  dashboard from one process/port, bound to this machine's Tailscale
  address instead of loopback. No separate Vite server, no new CORS
  surface -- api/__main__.py's existing non-loopback guard is not
  weakened, this script just satisfies it correctly (Tailscale is the
  "something in front of it" the guard's own help text asks for).

  Requires FUTURES_BOT_DATABASE_URL to already be set in the calling
  shell's environment before running this (contains credentials --
  never passed as a script parameter, never written to a file by this
  script, same convention every other secret in this project follows).
  Any failure stops immediately with what failed and how to fix it --
  same Write-Failure convention as start.ps1.

.PARAMETER Port
  Port to bind. Default 8000, matching every other entry point in this
  project.

.PARAMETER HostAddress
  Override the Tailscale IP auto-detection (e.g. if `tailscale ip -4`
  isn't available on this machine, or you want to bind a specific
  interface). Must not be a loopback address -- use start.ps1 instead
  for a local-only setup.
#>

param(
    [int]$Port = 8000,
    [string]$HostAddress = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

Write-Host "=== futures-bot team-deployment startup ===" -ForegroundColor Cyan
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

# 3. FUTURES_BOT_DATABASE_URL must already be set -- this script never
#    reads or writes it beyond checking presence, since it contains
#    credentials (same convention as MASSIVE_API_KEY/TRADOVATE_* --
#    see db/engine.py's DATABASE_URL_ENV docstring).
Write-Step "Checking FUTURES_BOT_DATABASE_URL"
if (-not $env:FUTURES_BOT_DATABASE_URL) {
    Write-Failure `
        "FUTURES_BOT_DATABASE_URL is not set in this shell" `
        "Team mode requires a shared Postgres/TimescaleDB. Set it before running this script, e.g.: `$env:FUTURES_BOT_DATABASE_URL = 'postgresql+psycopg://user:pass@<tailscale-ip>:5432/futures_bot'   See TEAM_DEPLOYMENT.md."
}
Write-Ok "FUTURES_BOT_DATABASE_URL is set"

# 4. Ensure Python packages (including the `db` extra -- sqlalchemy/
#    psycopg/alembic, not needed by a single-developer SQLite setup)
#    are installed and current, every run.
Write-Step "Installing/updating Python dependencies (pip install -e '.[db]')"
& $Venv.Python -m pip install -e "$RepoRoot[db]" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Failure `
        "pip install -e '.[db]' failed (exit code $LASTEXITCODE)" `
        "Run manually to see the full error: $($Venv.Python) -m pip install -e `"$RepoRoot[db]`""
}
Write-Ok "Python dependencies up to date"

# 5. Determine the bind address: explicit -HostAddress, or auto-detect
#    via the Tailscale CLI. Refuses a loopback address outright -- that's
#    what start.ps1 is for.
Write-Step "Determining bind address"
$BindHost = $HostAddress
if (-not $BindHost) {
    $tailscaleCmd = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if (-not $tailscaleCmd) {
        $tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
    }
    if (-not $tailscaleCmd) {
        Write-Failure `
            "Tailscale CLI not found on PATH, and -HostAddress was not given" `
            "Install Tailscale (https://tailscale.com/download) and make sure 'tailscale' is on PATH, or pass -HostAddress <ip> explicitly."
    }
    $BindHost = (& $tailscaleCmd.Source ip -4).Trim()
    if (-not $BindHost) {
        Write-Failure `
            "'tailscale ip -4' returned nothing" `
            "Confirm Tailscale is running and this machine is connected to the tailnet: tailscale status   then re-run, or pass -HostAddress <ip> explicitly."
    }
}
if ($BindHost -in @("127.0.0.1", "::1", "localhost")) {
    Write-Failure `
        "-HostAddress resolved to a loopback address ($BindHost)" `
        "Team mode needs a real, network-reachable address. Use scripts\start.ps1 instead for a local-only setup."
}
Write-Ok "Binding to $BindHost`:$Port"

# 5b. Ensure a Windows Firewall rule actually lets tailnet peers reach
#     this port. Binding uvicorn to the Tailscale IP is necessary but not
#     sufficient: Windows Firewall's Private-profile default is
#     BlockInbound, and there is no rule for this app/port until this
#     step creates one -- without it, this machine's own curl/browser
#     test against $BindHost succeeds (local delivery to an owned IP
#     never crosses the filtered network path) while a genuine remote
#     tailnet peer is silently dropped. This is not a hypothetical: it
#     was confirmed the hard way on 2026-07-28 (`netsh advfirewall show
#     privateprofile` showed BlockInbound with zero matching rule) --
#     "works locally, unreachable from another device" is exactly what a
#     missing rule looks like. See Confirm-TailscaleFirewallRule's own
#     docstring in _common.ps1.
Write-Step "Checking Windows Firewall for an inbound rule on port $Port"
Confirm-TailscaleFirewallRule -Port $Port | Out-Null

# 6. Build the frontend once. VITE_API_BASE_URL= (empty) bakes in
#    relative API paths, so the same build works from any Tailscale
#    address without a rebuild -- same convention Dockerfile.api and
#    deploy/DEPLOYMENT.md's manual VPS steps already use.
Write-Step "Building frontend (npm ci && VITE_API_BASE_URL= npm run build)"
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-Failure `
        "npm.cmd not found on PATH" `
        "Install Node.js from https://nodejs.org and make sure npm is on PATH, then re-run this script."
}
$FrontendDir = Join-Path $RepoRoot "frontend"
Push-Location $FrontendDir
& $npmCmd.Source ci --no-fund --no-audit | Out-Null
$npmInstallExit = $LASTEXITCODE
if ($npmInstallExit -eq 0) {
    $env:VITE_API_BASE_URL = ""
    & $npmCmd.Source run build | Out-Null
    $npmBuildExit = $LASTEXITCODE
    Remove-Item Env:\VITE_API_BASE_URL -ErrorAction SilentlyContinue
} else {
    $npmBuildExit = 1
}
Pop-Location
if ($npmInstallExit -ne 0) {
    Write-Failure `
        "npm ci failed in frontend\ (exit code $npmInstallExit)" `
        "Run manually to see the full error: cd frontend; npm ci"
}
if ($npmBuildExit -ne 0) {
    Write-Failure `
        "npm run build failed in frontend\ (exit code $npmBuildExit)" `
        "Run manually to see the full error: cd frontend; `$env:VITE_API_BASE_URL=''; npm run build"
}
$FrontendDist = Join-Path $FrontendDir "dist"
if (-not (Test-Path (Join-Path $FrontendDist "index.html"))) {
    Write-Failure `
        "frontend\dist\index.html not found after build" `
        "The build reported success but produced no output -- check frontend\dist\ manually."
}
Write-Ok "Frontend built ($FrontendDist)"

# 7. Free the bind port of any stale process (by-port lookup only works
#    for 127.0.0.1/local ports via Get-NetTCPConnection -- also checked
#    against the loopback ports in case a local start.ps1 instance is
#    still running and would otherwise collide once uvicorn binds
#    0.0.0.0-equivalently on this interface).
Write-Step "Freeing port $Port of any old processes"
Stop-ProcessOnPort -Port $Port -Label "backend"
if (-not (Wait-ForPortFree -Port $Port -TimeoutSeconds 10)) {
    Write-Failure `
        "Port $Port is still in use after attempting to stop it" `
        "Find what's using it: Get-NetTCPConnection -LocalPort $Port | Select OwningProcess   then Get-Process -Id <pid> and close it manually (may need an elevated PowerShell)."
}
Write-Ok "Port $Port is free"

# 8. Start the backend -- one process, serving both the API and the
#    just-built dashboard (api/app.py::_maybe_mount_frontend picks up
#    FUTURES_BOT_FRONTEND_DIST automatically).
Write-Step "Starting FastAPI backend (--allow-network-exposure)"
$StartupDir = Get-StartupDir -RepoRoot $RepoRoot
$BackendOutLog = Join-Path $StartupDir "team-backend.out.log"
$BackendErrLog = Join-Path $StartupDir "team-backend.err.log"
$env:FUTURES_BOT_FRONTEND_DIST = $FrontendDist
$backendProc = Start-Process -FilePath $Venv.Python `
    -ArgumentList "-m", "futures_bot.api", "--host", $BindHost, "--port", $Port, "--allow-network-exposure" `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $BackendOutLog -RedirectStandardError $BackendErrLog
Save-ProcessInfo -RepoRoot $RepoRoot -Name "team-backend" -ProcessId $backendProc.Id
Write-Ok "Backend process started (PID $($backendProc.Id))"

# 9. Wait until it actually responds -- on the bind address, not
#    loopback, since --allow-network-exposure means loopback may not
#    even be reachable depending on the interface.
$TeamUrl = "http://$BindHost`:$Port"
Write-Step "Waiting for backend to respond at $TeamUrl"
if (-not (Wait-ForHttp -Url "$TeamUrl/api/system/overview" -TimeoutSeconds 30)) {
    Write-Failure `
        "Backend did not respond within 30 seconds" `
        "Check the logs: Get-Content '$BackendOutLog' -Tail 50   and Get-Content '$BackendErrLog' -Tail 50 . Common causes: FUTURES_BOT_DATABASE_URL pointing at an unreachable server, a missing dependency, or the port still held by something step 7 couldn't stop."
}
Write-Ok "Backend is responding"

# 10. Green success message.
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " futures-bot (team mode) is running"        -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host " URL (share with teammates): $TeamUrl"       -ForegroundColor Green
Write-Host " Database:  $($env:FUTURES_BOT_DATABASE_URL -replace '://[^@]+@', '://***:***@')" -ForegroundColor Green
Write-Host " API:       responding (200 OK)"              -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Logs:  $StartupDir\team-backend.{out,err}.log" -ForegroundColor DarkGray
Write-Host "Stop:  Stop-Process -Id $($backendProc.Id) -Force   (or scripts\stop.ps1 -- it stops by-port, works for this too)" -ForegroundColor DarkGray
