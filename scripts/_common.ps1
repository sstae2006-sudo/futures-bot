<#
.SYNOPSIS
  Shared helpers for start.ps1 / stop.ps1 / restart.ps1 / status.ps1.
  Dot-source this file -- never run it directly.

.DESCRIPTION
  Everything here is read-only or explicitly scoped to this project's
  own two ports (8000 backend, 5173 frontend, both hardcoded to
  127.0.0.1 -- see src/futures_bot/api/__main__.py and
  frontend/vite.config.ts). By-port process lookup (Get-NetTCPConnection)
  is the authoritative way this project's scripts find and stop "our"
  backend/frontend -- it returns the PID that actually holds the
  listening socket, not a wrapper process (verified empirically: a
  `npm run dev` launch's real node/vite process, not the npm.cmd/cmd.exe
  shell around it). PID files under .startup/ are written purely for
  status.ps1's informational display -- never used to decide what to
  kill.
#>

$Script:BackendPort = 8000
$Script:FrontendPort = 5173
$Script:BackendUrl = "http://127.0.0.1:$BackendPort"
$Script:FrontendUrl = "http://127.0.0.1:$FrontendPort"
$Script:HealthPath = "/api/system/overview"

function Write-Step {
    param([string]$Message)
    Write-Host "-> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "   OK: $Message" -ForegroundColor Green
}

function Write-WarnLine {
    param([string]$Message)
    Write-Host "   WARN: $Message" -ForegroundColor Yellow
}

function Write-Failure {
    <#
    Prints a red "what failed" + yellow "how to fix" block and
    terminates the calling script immediately (exit 1). Every hard
    failure in start/stop/restart.ps1 routes through this one
    function, so "stop immediately, explain what and how to fix,
    never silently continue" is enforced the same way everywhere
    instead of being reimplemented per script.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$Fix
    )
    Write-Host ""
    Write-Host "FAILED: $What" -ForegroundColor Red
    Write-Host "How to fix: $Fix" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

function Assert-RepoRoot {
    <#
    Resolves the repo root from THIS file's own location
    ($PSScriptRoot), never from the caller's current directory --
    that's what makes every script work "from anywhere." Verifies
    it's actually the futures-bot repo, not just any directory that
    happens to contain a scripts\ folder, before trusting it.
    #>
    $root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $pyproject = Join-Path $root "pyproject.toml"
    $claudeMd = Join-Path $root "CLAUDE.md"
    $pkg = Join-Path $root "src\futures_bot"

    if (-not (Test-Path $pyproject)) {
        Write-Failure `
            "Not in the futures-bot repository" `
            "Expected to find pyproject.toml at '$root'. These scripts must live in <repo>\scripts\ -- if you moved this file, put it back there."
    }

    $content = Get-Content $pyproject -Raw
    if ($content -notmatch 'name\s*=\s*"futures-bot"') {
        Write-Failure `
            "Wrong repository" `
            "'$pyproject' exists but its [project] name isn't 'futures-bot'. Is scripts\ nested inside the wrong repo?"
    }

    if (-not (Test-Path $claudeMd) -or -not (Test-Path $pkg)) {
        Write-Failure `
            "Repository looks incomplete" `
            "Found pyproject.toml at '$root' but CLAUDE.md or src\futures_bot is missing. Re-clone or check out a complete working tree."
    }

    return $root
}

function Assert-Venv {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $venvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"

    if (-not (Test-Path $venvPython) -or -not (Test-Path $venvActivate)) {
        Write-Failure `
            "No virtual environment found at .venv\Scripts\python.exe" `
            "Run: python -m venv .venv   then re-run this script."
    }

    return @{ Python = $venvPython; Activate = $venvActivate }
}

function Get-ProcessIdsOnPort {
    <#
    Returns the PID(s) actually listening on $Port. Prefers
    Get-NetTCPConnection (ships with Windows 10/11's NetTCPIP module);
    falls back to parsing `netstat -ano` if that cmdlet isn't
    available for some reason.
    #>
    param([Parameter(Mandatory = $true)][int]$Port)

    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($conns) {
            return @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
        }
        return @()
    }

    $lines = netstat -ano | Select-String -Pattern "LISTENING" | Select-String -Pattern ":$Port\s"
    $result = @()
    foreach ($line in $lines) {
        $parts = ($line.Line -split '\s+') | Where-Object { $_ -ne '' }
        if ($parts.Length -gt 0) {
            $result += [int]$parts[-1]
        }
    }
    return @($result | Select-Object -Unique)
}

function Stop-ProcessOnPort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $ids = Get-ProcessIdsOnPort -Port $Port
    foreach ($processId in $ids) {
        try {
            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-WarnLine "Killing existing $Label process (PID $processId, $($proc.ProcessName)) on port $Port"
                Stop-Process -Id $processId -Force -ErrorAction Stop
            }
        } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
            # Already exited between lookup and kill -- fine, not an error.
        } catch {
            Write-Failure `
                "Could not stop the process on port $Port (PID $processId)" `
                "Close it manually (Task Manager, or Stop-Process -Id $processId -Force from an elevated PowerShell), then re-run this script."
        }
    }
    if ($ids.Count -gt 0) {
        Start-Sleep -Milliseconds 500
    }
}

function Wait-ForPortFree {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = 15
    )
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if ((Get-ProcessIdsOnPort -Port $Port).Count -eq 0) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return (Get-ProcessIdsOnPort -Port $Port).Count -eq 0
}

function Wait-ForHttp {
    <#
    Retry-loop used by start.ps1's "wait until it actually responds"
    steps. Always uses -UseBasicParsing: without it, a machine whose
    Internet Explorer first-run wizard was never completed throws an
    unrelated "first launch configuration is not complete" error
    instead of a clean connection failure -- a real trap for a script
    meant to run unattended right after a reboot.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 30
    )
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                return $true
            }
        } catch {
            # Not up yet -- keep polling.
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Test-HttpOk {
    <#
    Single-shot reachability check for status.ps1 -- never loops, so
    a down service reports "not reachable" in ~2 seconds, not after
    start.ps1's much longer patience budget.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 2
    )
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-StartupDir {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $dir = Join-Path $RepoRoot ".startup"
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
    return $dir
}

function Save-ProcessInfo {
    <#
    Informational only (status.ps1's "started by this script" display)
    -- never used to decide what to kill. See the by-port functions
    above for the actual, authoritative stop mechanism.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$ProcessId
    )
    $dir = Get-StartupDir -RepoRoot $RepoRoot
    Set-Content -Path (Join-Path $dir "$Name.pid") -Value $ProcessId -Encoding utf8
}

function Get-SavedProcessId {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $dir = Get-StartupDir -RepoRoot $RepoRoot
    $file = Join-Path $dir "$Name.pid"
    if (Test-Path $file) {
        $val = (Get-Content $file -Raw).Trim()
        if ($val -match '^\d+$') {
            return [int]$val
        }
    }
    return $null
}

function Remove-ProcessInfo {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $dir = Get-StartupDir -RepoRoot $RepoRoot
    $file = Join-Path $dir "$Name.pid"
    if (Test-Path $file) {
        Remove-Item $file -Force
    }
}

#: Tailscale's own CGNAT range (https://tailscale.com/kb/1015/100.x-addresses)
#: -- every tailnet peer's IP falls inside this block, nothing outside it
#: does. Scoping the firewall rule's RemoteAddress to this range (rather
#: than "any") means the rule only ever opens the port to actual tailnet
#: traffic, matching --allow-network-exposure's own stated trust model
#: (api/__main__.py: "only pass this once something in front of it
#: actually restricts access" -- Tailscale is that something).
$Script:TailscaleCgnatRange = "100.64.0.0/10"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Confirm-TailscaleFirewallRule {
    <#
    .SYNOPSIS
      Idempotently ensures an inbound firewall rule exists letting
      tailnet peers (only -- see $TailscaleCgnatRange above) reach
      $Port. Team mode's single guaranteed-reproducible failure mode is
      "works from this machine, unreachable from another device": binding
      uvicorn to the Tailscale IP is necessary but not sufficient --
      Windows Firewall's Private-profile default is BlockInbound, and a
      same-machine curl/Invoke-WebRequest against the box's own Tailscale
      IP is delivered locally without ever crossing the filtered network
      path, so it succeeds regardless of whether a real remote peer would
      be dropped. Confirmed empirically: `netsh advfirewall show
      privateprofile` reports "Firewall Policy: BlockInbound,AllowOutbound"
      with zero pre-existing rule for python.exe or this port.

    .DESCRIPTION
      Checks for an existing enabled rule by name first (safe to call on
      every start-team.ps1 run). If missing and this process is already
      elevated, creates it directly. If missing and NOT elevated,
      self-elevates *only* the rule-creation step via a separate
      `Start-Process -Verb RunAs` (one UAC prompt), then re-checks --
      never silently continues without the rule actually existing. A
      declined UAC prompt is reported as a clear warning (with the exact
      manual command) rather than a hard failure, since local access
      still works either way.

      The elevation wait is bounded ($ElevationTimeoutSeconds, default 45)
      rather than an indefinite `Start-Process -Wait` -- confirmed the hard
      way (2026-07-28) that an unattended run (nobody at the console to
      click the UAC prompt) would otherwise hang this entire script
      forever. A first attempt bounded it with `$proc.WaitForExit(ms)`
      instead of `-Wait`, but that turned out to be just as unreliable:
      `Start-Process -Verb RunAs -PassThru`'s returned Process object
      doesn't reliably support `WaitForExit` across the UAC elevation
      boundary (confirmed empirically -- it kept blocking past the
      requested timeout with a live, unanswered `consent.exe` prompt still
      open). Polls for a completion marker *file* instead, written by the
      elevated child itself -- this only depends on the filesystem, not on
      any cross-elevation process-handle relationship, so it can't get
      stuck the same way. On timeout, the elevated helper (and any pending
      UAC prompt) is left alone rather than force-closed, in case someone
      answers it after this script moves on.
    #>
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$ElevationTimeoutSeconds = 45
    )

    $ruleName = "futures-bot Team Mode ($Port/tcp, Tailscale)"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing -and $existing.Enabled -eq "True") {
        Write-Ok "Firewall rule '$ruleName' already present"
        return $true
    }

    $ruleArgs = "-DisplayName '$ruleName' -Direction Inbound -Action Allow -Protocol TCP " +
        "-LocalPort $Port -RemoteAddress $TailscaleCgnatRange -Profile Any"

    if (Test-IsAdministrator) {
        try {
            Invoke-Expression "New-NetFirewallRule $ruleArgs | Out-Null"
            Write-Ok "Created firewall rule '$ruleName' (already elevated)"
            return $true
        } catch {
            Write-WarnLine "Could not create firewall rule even though elevated: $($_.Exception.Message)"
            return $false
        }
    }

    $manualCommandWarning = (
        "Could not create the firewall rule automatically (UAC prompt declined, timed out, or " +
        "elevation failed). Other devices on your tailnet will NOT be able to reach this backend " +
        "until it exists. Run this once from an elevated PowerShell: New-NetFirewallRule $ruleArgs"
    )

    $marker = Join-Path $env:TEMP "futures-bot-firewall-rule-$([guid]::NewGuid().ToString('N')).marker"
    # The elevated child writes "OK" or "ERROR: ..." to $marker as its very
    # last act, inside try/finally so a mid-script failure still leaves a
    # marker behind instead of the parent polling forever for one that will
    # never come.
    $scriptBlock = (
        "try { New-NetFirewallRule $ruleArgs | Out-Null; 'OK' | Set-Content -Path '$marker' -Encoding utf8 } " +
        "catch { `"ERROR: `$(`$_.Exception.Message)`" | Set-Content -Path '$marker' -Encoding utf8 }"
    )

    Write-WarnLine (
        "No inbound firewall rule for port $Port yet -- requesting elevation (one UAC prompt, " +
        "waiting up to ${ElevationTimeoutSeconds}s) to create it"
    )

    # The launch itself -- not just waiting for it afterward -- runs inside
    # a background job. Confirmed empirically (2026-07-28) that
    # `Start-Process -Verb RunAs` can block *synchronously* until the UAC
    # prompt is answered, regardless of -Wait/-PassThru or anything done
    # after it returns -- there is nothing to "wait with a timeout" on if
    # the launch call itself never returns control. Wait-Job -Timeout,
    # unlike Process.WaitForExit, monitors the job from an entirely
    # separate process and reliably returns on schedule no matter what the
    # job's own code is blocked on; Stop-Job then unblocks it.
    $job = Start-Job -ScriptBlock {
        param($EncodedCommand)
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-EncodedCommand", $EncodedCommand `
            -Verb RunAs -WindowStyle Hidden
    } -ArgumentList ([Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($scriptBlock)))

    $deadline = (Get-Date).AddSeconds($ElevationTimeoutSeconds)
    $result = $null
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $marker) {
            $result = (Get-Content $marker -Raw -ErrorAction SilentlyContinue)
            break
        }
        Start-Sleep -Milliseconds 500
    }
    # Stop-Job unblocks the launcher job itself (it doesn't touch
    # consent.exe/the elevated child directly -- a still-pending prompt is
    # left alone, same intent as before, just now the harness that would
    # otherwise wait on it forever is reliably freed).
    Stop-Job -Job $job -ErrorAction SilentlyContinue
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    Remove-Item $marker -Force -ErrorAction SilentlyContinue

    if ($null -eq $result) {
        Write-WarnLine (
            "Elevation prompt was not answered within ${ElevationTimeoutSeconds}s -- continuing " +
            "without the firewall rule (the pending prompt, if any, is left alone rather than " +
            "force-closed, in case someone answers it after this script moves on)."
        )
        Write-WarnLine $manualCommandWarning
        return $false
    }
    if ($result.Trim() -ne "OK") {
        Write-WarnLine "Elevated rule creation reported an error: $($result.Trim())"
        Write-WarnLine $manualCommandWarning
        return $false
    }

    $recheck = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($recheck -and $recheck.Enabled -eq "True") {
        Write-Ok "Created firewall rule '$ruleName' (via elevation)"
        return $true
    }
    Write-WarnLine "Firewall rule still missing after attempting elevation -- other tailnet devices will not be able to reach this backend."
    return $false
}
