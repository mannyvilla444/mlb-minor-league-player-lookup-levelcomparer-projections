# Creates a "Level Stats" shortcut on the desktop, pointing at the launcher.
# Run it through "Create Desktop Shortcut.bat" so PowerShell's execution
# policy is bypassed for this one script without changing any system setting.

param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$launcher = Join-Path $ProjectDir 'Start Level Stats.bat'
if (-not (Test-Path $launcher)) {
    Write-Host ""
    Write-Host "  ERROR: could not find 'Start Level Stats.bat' in:"
    Write-Host "         $ProjectDir"
    Write-Host ""
    exit 1
}

$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }
$linkPath = Join-Path $desktop 'Level Stats.lnk'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $ProjectDir
$shortcut.Description = 'MLB and minor-league hitting stats by level'

$icon = Join-Path $ProjectDir 'app\static\levelstats.ico'
if (Test-Path $icon) { $shortcut.IconLocation = $icon }

$shortcut.Save()

Write-Host ""
Write-Host "  Shortcut created:"
Write-Host "    $linkPath"
Write-Host ""
Write-Host "  Double-click 'Level Stats' on your desktop to start the app."
Write-Host ""
