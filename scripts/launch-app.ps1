<#
.SYNOPSIS
  Desktop-app mode: opens futures-bot in its own window (no browser tabs,
  no terminal) and shuts the backend down again when the window closes.
  Meant to be started from the shortcut that scripts\install-shortcut.ps1
  creates, which runs this script with a hidden console.

.DESCRIPTION
  Single process, single port: the FastAPI backend serves both the API and
  the built React dashboard (api/app.py's _maybe_mount_frontend), so no
  Vite dev server is involved. Steps:

    1. Verify the repo and venv (same checks as start.ps1).
    2. Rebuild frontend\dist only when the frontend source is newer than
       the last build -- a stale dist silently serves an old UI.
    3. Reuse a backend that is already serving the dashboard on :8000
       (e.g. one start.ps1 started); otherwise start one, hidden.
    4. Open an app window (Edge, falling back to Chrome) on a dedicated
       browser profile under .startup\app-profile. The dedicated profile
       matters: with the user's normal profile, a running browser absorbs
       the new window and the launched process exits at once, so this
       script couldn't tell when the window closed.
    5. When that window closes, stop the backend -- but only if step 3
       started it.

  Windows only, like the rest of scripts\. Development still uses
  start.ps1 (Vite with hot reload on :5173); this is for using the app.

  Failures: the console is hidden, so Write-Failure is overridden below to
  also show a message box and append to .startup\app-launcher.log instead
  of only printing to a console nobody can see.
#>

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$Script:LauncherLog = $null

function Write-LauncherLog {
    param([string]$Message)
    if ($Script:LauncherLog) {
        Add-Content -Path $Script:LauncherLog -Value "$(Get-Date -Format s)  $Message" -Encoding utf8
    }
}

# Shadows _common.ps1's Write-Failure for this script only: same contract
# (what failed + how to fix, then exit 1), but visible without a console.
function Write-Failure {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$Fix
    )
    Write-Host "FAILED: $What" -ForegroundColor Red
    Write-Host "How to fix: $Fix" -ForegroundColor Yellow
    Write-LauncherLog "FAILED: $What | How to fix: $Fix"
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show(
            "$What`n`nHow to fix: $Fix",
            "futures-bot could not start",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error)
    } catch {
        # No desktop session (e.g. run over SSH): the console/log output above is all we can do.
    }
    exit 1
}

function Get-NewestWriteTime {
    <# Latest LastWriteTime across the given files/directories (recursive). #>
    param([string[]]$Paths)
    $newest = [datetime]::MinValue
    foreach ($p in $Paths) {
        if (-not (Test-Path $p)) { continue }
        $item = Get-Item $p
        $candidates = if ($item.PSIsContainer) { Get-ChildItem $p -Recurse -File } else { @($item) }
        foreach ($f in $candidates) {
            if ($f.LastWriteTime -gt $newest) { $newest = $f.LastWriteTime }
        }
    }
    return $newest
}

function Find-AppBrowser {
    <# Edge ships with Windows; Chrome is the fallback. Both support --app. #>
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

function Test-ServesDashboard {
    <# True when :8000 answers "/" with the dashboard's HTML, not just the API. #>
    try {
        $resp = Invoke-WebRequest -Uri "$BackendUrl/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return ($resp.StatusCode -eq 200) -and ($resp.Content -match '<div id="root">')
    } catch {
        return $false
    }
}

# Last line of defence for "no silent failures": the console is hidden, so an
# unexpected terminating error would otherwise just end the script with no
# window, no message and no log line.
trap {
    Write-Failure `
        "Unexpected error: $($_.Exception.Message)" `
        "See .startup\app-launcher.log. If it keeps happening, run scripts\launch-app.ps1 from a PowerShell window to see the full error."
}

# 1. Repository + venv
$RepoRoot = Assert-RepoRoot
Set-Location $RepoRoot
$StartupDir = Get-StartupDir -RepoRoot $RepoRoot
$Script:LauncherLog = Join-Path $StartupDir "app-launcher.log"
Write-LauncherLog "---- launch ----"
$Venv = Assert-Venv -RepoRoot $RepoRoot

# 2. Frontend build, only when stale
$FrontendDir = Join-Path $RepoRoot "frontend"
$DistDir = Join-Path $FrontendDir "dist"
$DistIndex = Join-Path $DistDir "index.html"
$sourceTime = Get-NewestWriteTime -Paths @(
    (Join-Path $FrontendDir "src"),
    (Join-Path $FrontendDir "public"),
    (Join-Path $FrontendDir "index.html"),
    (Join-Path $FrontendDir "package.json"),
    (Join-Path $FrontendDir "vite.config.ts")
)
$buildTime = if (Test-Path $DistIndex) { (Get-Item $DistIndex).LastWriteTime } else { [datetime]::MinValue }
if ($sourceTime -gt $buildTime) {
    Write-Step "Building the dashboard (frontend changed since the last build)"
    Write-LauncherLog "building frontend (source $sourceTime > build $buildTime)"
    $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        Write-Failure `
            "npm.cmd not found on PATH, and the dashboard needs rebuilding" `
            "Install Node.js from https://nodejs.org (npm comes with it), then open the app again."
    }
    # Run npm through Start-Process with file redirection, never `& npm ... *>> log`:
    # in Windows PowerShell 5.1, redirecting a native command's stderr under
    # $ErrorActionPreference = "Stop" turns any stderr line (vite's chunk-size
    # warning, npm notices) into a terminating error, which killed this script
    # silently mid-build. Start-Process also writes plain UTF-8/ANSI logs.
    $BuildLog = Join-Path $StartupDir "app-build.log"
    $BuildErrLog = Join-Path $StartupDir "app-build.err.log"
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        $install = Start-Process -FilePath $npmCmd.Source -ArgumentList "install", "--no-fund", "--no-audit" `
            -WorkingDirectory $FrontendDir -WindowStyle Hidden -Wait -PassThru `
            -RedirectStandardOutput $BuildLog -RedirectStandardError $BuildErrLog
        if ($install.ExitCode -ne 0) {
            Write-Failure "npm install failed (exit code $($install.ExitCode))" "See $BuildErrLog, or run: cd frontend; npm install"
        }
    }
    $build = Start-Process -FilePath $npmCmd.Source -ArgumentList "run", "build" `
        -WorkingDirectory $FrontendDir -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput $BuildLog -RedirectStandardError $BuildErrLog
    if ($build.ExitCode -ne 0) {
        Write-Failure `
            "Building the dashboard failed (exit code $($build.ExitCode))" `
            "See $BuildLog and $BuildErrLog (usually a TypeScript error from a recent change), or run: cd frontend; npm run build"
    }
    Write-Ok "Dashboard built"
}

# 3. Backend: reuse one that already serves the dashboard, else start our own
$StartedBackend = $false
if (Test-ServesDashboard) {
    Write-Ok "Reusing the backend already running on $BackendUrl"
    Write-LauncherLog "reusing existing backend"
} else {
    # Something on :8000 that doesn't serve the dashboard (an API started
    # before dist existed, or a stale process) gets replaced.
    Stop-ProcessOnPort -Port $BackendPort -Label "backend"
    if (-not (Wait-ForPortFree -Port $BackendPort -TimeoutSeconds 10)) {
        Write-Failure `
            "Port $BackendPort is in use and could not be freed" `
            "Close whatever is using it (Get-NetTCPConnection -LocalPort $BackendPort), or run scripts\stop.ps1, then open the app again."
    }
    Write-Step "Starting the backend"
    $env:FUTURES_BOT_FRONTEND_DIST = $DistDir
    $backendProc = Start-Process -FilePath $Venv.Python -ArgumentList "-m", "futures_bot.api" `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $StartupDir "app-backend.out.log") `
        -RedirectStandardError (Join-Path $StartupDir "app-backend.err.log")
    Save-ProcessInfo -RepoRoot $RepoRoot -Name "backend" -ProcessId $backendProc.Id
    $StartedBackend = $true
    Write-LauncherLog "started backend PID $($backendProc.Id)"
    if (-not (Wait-ForHttp -Url "$BackendUrl$HealthPath" -TimeoutSeconds 45)) {
        Stop-ProcessOnPort -Port $BackendPort -Label "backend"
        Write-Failure `
            "The backend did not respond within 45 seconds" `
            "See .startup\app-backend.err.log. Common causes: a missing dependency (run scripts\start.ps1 once, which reinstalls them) or a config.yaml error."
    }
    if (-not (Test-ServesDashboard)) {
        Stop-ProcessOnPort -Port $BackendPort -Label "backend"
        Write-Failure `
            "The backend started but is not serving the dashboard" `
            "Check that frontend\dist\index.html exists (cd frontend; npm run build), then open the app again."
    }
    Write-Ok "Backend is up"
}

# 4. App window
$Browser = Find-AppBrowser
if (-not $Browser) {
    if ($StartedBackend) { Stop-ProcessOnPort -Port $BackendPort -Label "backend" }
    Write-Failure `
        "Neither Microsoft Edge nor Google Chrome was found" `
        "Install either browser; the app window uses its --app mode."
}
$ProfileDir = Join-Path $StartupDir "app-profile"
Write-Step "Opening the app window"
Write-LauncherLog "opening $Browser"
$windowProc = Start-Process -FilePath $Browser -PassThru -ArgumentList @(
    "--app=$BackendUrl/",
    "--user-data-dir=`"$ProfileDir`"",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1440,920"
)

# 5. Wait for the window to close, then clean up what we started
$windowProc.WaitForExit()
Write-LauncherLog "app window closed"
if ($StartedBackend) {
    Stop-ProcessOnPort -Port $BackendPort -Label "backend"
    Remove-ProcessInfo -RepoRoot $RepoRoot -Name "backend"
    Write-LauncherLog "stopped backend"
}
