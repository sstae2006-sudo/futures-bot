<#
.SYNOPSIS
  Creates "futures-bot" shortcuts on the Desktop and in the Start Menu that
  open the app in its own window via scripts\launch-app.ps1. Run once per
  machine (re-running is safe; it overwrites the shortcuts). Pass -Remove
  to delete them again.

.DESCRIPTION
  The shortcut starts launch-app.ps1 with a hidden console, so double-clicking
  it shows only the app window. The icon (the brass diamond brand mark) is
  drawn here with System.Drawing and written to .startup\futures-bot.ico,
  so no binary asset lives in the repo and each machine generates its own.
  Shortcuts point at this checkout's absolute path: move the repo and you
  re-run this script.
#>

param([switch]$Remove)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$RepoRoot = Assert-RepoRoot
$StartupDir = Get-StartupDir -RepoRoot $RepoRoot
$ShortcutName = "futures-bot.lnk"
$Targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) $ShortcutName),
    (Join-Path ([Environment]::GetFolderPath("Programs")) $ShortcutName)
)

if ($Remove) {
    foreach ($t in $Targets) {
        if (Test-Path $t) {
            Remove-Item $t -Force
            Write-Ok "Removed $t"
        }
    }
    exit 0
}

function New-AppIcon {
    <#
    Writes a multi-size .ico (16-256px, PNG-compressed entries, supported
    since Windows Vista): a brass diamond on the dark sidebar colour,
    matching .brand-mark in frontend/src/index.css.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    Add-Type -AssemblyName System.Drawing
    $sizes = @(16, 32, 48, 256)
    $pngs = @()
    foreach ($s in $sizes) {
        $bmp = New-Object System.Drawing.Bitmap $s, $s
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.Clear([System.Drawing.Color]::Transparent)
        # Rounded dark tile
        $r = [math]::Max(2, [int]($s * 0.22))
        $tile = New-Object System.Drawing.Drawing2D.GraphicsPath
        $tile.AddArc(0, 0, $r * 2, $r * 2, 180, 90)
        $tile.AddArc($s - $r * 2 - 1, 0, $r * 2, $r * 2, 270, 90)
        $tile.AddArc($s - $r * 2 - 1, $s - $r * 2 - 1, $r * 2, $r * 2, 0, 90)
        $tile.AddArc(0, $s - $r * 2 - 1, $r * 2, $r * 2, 90, 90)
        $tile.CloseFigure()
        $g.FillPath((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 29, 34, 44))), $tile)
        # Brass diamond
        $c = $s / 2.0
        $h = $s * 0.30
        $diamond = [System.Drawing.PointF[]]@(
            (New-Object System.Drawing.PointF $c, ($c - $h)),
            (New-Object System.Drawing.PointF ($c + $h), $c),
            (New-Object System.Drawing.PointF $c, ($c + $h)),
            (New-Object System.Drawing.PointF ($c - $h), $c)
        )
        $g.FillPolygon((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 217, 168, 78))), $diamond)
        $g.Dispose()
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        $pngs += , $ms.ToArray()
    }
    # ICONDIR + one ICONDIRENTRY per image, then the PNG payloads.
    $out = New-Object System.IO.MemoryStream
    $w = New-Object System.IO.BinaryWriter $out
    $w.Write([uint16]0); $w.Write([uint16]1); $w.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($i = 0; $i -lt $sizes.Count; $i++) {
        $dim = if ($sizes[$i] -ge 256) { 0 } else { $sizes[$i] }  # 0 means 256
        $w.Write([byte]$dim); $w.Write([byte]$dim); $w.Write([byte]0); $w.Write([byte]0)
        $w.Write([uint16]1); $w.Write([uint16]32)
        $w.Write([uint32]$pngs[$i].Length); $w.Write([uint32]$offset)
        $offset += $pngs[$i].Length
    }
    foreach ($p in $pngs) { $w.Write($p) }
    $w.Flush()
    [System.IO.File]::WriteAllBytes($Path, $out.ToArray())
    $w.Dispose()
}

Write-Step "Drawing the app icon"
$IconPath = Join-Path $StartupDir "futures-bot.ico"
New-AppIcon -Path $IconPath
Write-Ok "Icon written to $IconPath"

Write-Step "Creating shortcuts"
$LaunchScript = Join-Path $PSScriptRoot "launch-app.ps1"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$shell = New-Object -ComObject WScript.Shell
foreach ($t in $Targets) {
    $lnk = $shell.CreateShortcut($t)
    $lnk.TargetPath = $PowerShellExe
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LaunchScript`""
    $lnk.WorkingDirectory = $RepoRoot
    $lnk.IconLocation = "$IconPath,0"
    $lnk.Description = "Open futures-bot in its own window"
    # 7 = minimized: the console flashes minimized for a moment before
    # -WindowStyle Hidden takes effect, instead of popping up full size.
    $lnk.WindowStyle = 7
    $lnk.Save()
    Write-Ok "Created $t"
}

Write-Host ""
Write-Host "Done. Double-click 'futures-bot' on your Desktop or in the Start Menu." -ForegroundColor Green
Write-Host "Remove later with: scripts\install-shortcut.ps1 -Remove" -ForegroundColor DarkGray
