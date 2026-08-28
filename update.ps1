# One-step update: extract a new sdtools zip over this folder, then reinstall.
#
# Why this exists: `uv tool install` COPIES the code into an isolated location,
# so replacing files in C:\sdtools does nothing until the install is re-run —
# and re-running the install before extracting silently reinstalls the OLD code.
# This script does both, in the right order, and prints the resulting version.
#
#   powershell -ExecutionPolicy Bypass -File .\update.ps1
#     (finds the newest sdtools*.zip in your Downloads folder)
#
#   powershell -ExecutionPolicy Bypass -File .\update.ps1 -Zip C:\path\to\sdtools-windows.zip

param([string]$Zip = "")

$ErrorActionPreference = "Stop"
$target = $PSScriptRoot

if (-not $Zip) {
    $dl = Join-Path $env:USERPROFILE "Downloads"
    $found = Get-ChildItem -Path $dl -Filter "sdtools*.zip" -Recurse -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $found) {
        Write-Host "No sdtools*.zip found under $dl - pass -Zip <path>" -ForegroundColor Red
        exit 1
    }
    $Zip = $found.FullName
}

if (-not (Test-Path $Zip)) {
    Write-Host "Zip not found: $Zip" -ForegroundColor Red
    exit 1
}

Write-Host "Updating $target" -ForegroundColor Cyan
Write-Host "  from $Zip  ($(Get-Item $Zip | Select-Object -ExpandProperty LastWriteTime))"

# Some zips wrap everything in a top-level 'sdtools/' folder; ours also
# contains a 'sdtools/' PYTHON PACKAGE at the payload root. Only treat the
# subfolder as the root when it (and not the zip root) holds pyproject.toml,
# or we'd spray the package files over the install dir and update nothing.
$tmp = Join-Path $env:TEMP ("sdtools-update-" + [guid]::NewGuid().ToString("N").Substring(0,8))
Expand-Archive -Path $Zip -DestinationPath $tmp -Force
$root = $tmp
$wrapped = Join-Path $tmp "sdtools"
if (-not (Test-Path (Join-Path $tmp "pyproject.toml")) -and
    (Test-Path (Join-Path $wrapped "pyproject.toml"))) { $root = $wrapped }

# Code and docs are replaced. tools/ and envs/ are MERGED, so anything you
# wrapped or locked yourself survives an update. robocopy, not Copy-Item:
# PS 5.1's Copy-Item -Recurse nests instead of merging when a destination
# subdirectory already exists, silently leaving the old files in place.
robocopy $root $target /E /NFL /NDL /NP /NJH /NJS /R:1 /W:1 | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "robocopy failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}
Remove-Item $tmp -Recurse -Force

Write-Host "Files updated. Reinstalling..." -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File (Join-Path $target "install.ps1")
