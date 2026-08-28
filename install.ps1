# sdtools installer for Windows.
#
#   1. Extract the sdtools folder somewhere permanent, e.g. C:\sdtools
#      (NOT a "Downloads\New folder" — the tools/ and envs/ dirs live here).
#   2. In PowerShell, from inside that folder:
#        powershell -ExecutionPolicy Bypass -File .\install.ps1
#   3. Open a NEW PowerShell window and run:  sdtools list
#
# What it does: installs uv (the Python/package manager the console uses for
# environments) if missing, installs pixi (conda-forge env manager — needed by
# the `kind: pixi` environments: GDAL/PDAL stacks) if missing, then installs
# sdtools as an isolated uv tool with its own Python 3.12 — nothing touches
# any Python you already have.
#
# Optional: -PrebuildEnvs also builds every locked environment now (downloads
# packages once) so the first tool run on this machine is instant.

param([switch]$PrebuildEnvs)

$ErrorActionPreference = "Stop"

if (-not (Test-Path "$PSScriptRoot\pyproject.toml")) {
    Write-Host "Run this from inside the extracted sdtools folder." -ForegroundColor Red
    exit 1
}

# --- 1. uv ---------------------------------------------------------------
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "Installing uv..." -ForegroundColor Cyan
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $uv = Get-Command uv -ErrorAction Stop
}
Write-Host "uv: $((uv --version))" -ForegroundColor Green

# --- 1b. pixi (conda-forge envs: GDAL/PDAL stacks) ------------------------
$pixi = Get-Command pixi -ErrorAction SilentlyContinue
if (-not $pixi) {
    Write-Host "Installing pixi..." -ForegroundColor Cyan
    irm https://pixi.sh/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.pixi\bin;$env:Path"
    $pixi = Get-Command pixi -ErrorAction Stop
}
Write-Host "pixi: $((pixi --version))" -ForegroundColor Green

# --- 2. sdtools as an isolated uv tool -----------------------------------
Write-Host "Installing sdtools from $PSScriptRoot ..." -ForegroundColor Cyan
uv tool install --python 3.12 --force --from "$PSScriptRoot" sdtools
uv tool update-shell | Out-Null   # ensure the shims dir is on PATH

# --- 3. point the console at THIS folder's tools/ and envs/ --------------
$cfgDir = "$env:USERPROFILE\.sdtools"
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$cfg = "$cfgDir\config.toml"
if (-not (Test-Path $cfg)) {
    $toolsDir = ($PSScriptRoot -replace '\\', '\\') + '\\tools'
    @"
user = "$env:USERNAME"

[tools]
dir = "$toolsDir"

# The fleet config (config.toml in the install dir) ships the api url.
# Add this machine's own key once provisioned (submit for operators,
# ingest for worker agents):
# [api]
# key = "sdt_inge_..."
"@ | Set-Content $cfg
    Write-Host "Wrote $cfg (edit it to add your API url/key later)" -ForegroundColor Green
} else {
    Write-Host "$cfg already exists - left untouched" -ForegroundColor Yellow
}

# --- 4. prove which build is now live -----------------------------------
$installed = & "$env:USERPROFILE\.local\bin\sdtools.exe" version 2>$null
if (-not $installed) { $installed = (sdtools version 2>$null) }
Write-Host ""
Write-Host "Installed sdtools version: $installed" -ForegroundColor Cyan
Write-Host "(If that is not the version you expected, the zip was not extracted"
Write-Host " over this folder before running install.ps1.)"

# --- 5. optionally prebuild every locked environment ----------------------
if ($PrebuildEnvs) {
    Write-Host ""
    Write-Host "Prebuilding environments from lockfiles..." -ForegroundColor Cyan
    $sdt = "$env:USERPROFILE\.local\bin\sdtools.exe"
    if (-not (Test-Path $sdt)) { $sdt = "sdtools" }
    Get-ChildItem -Directory "$PSScriptRoot\envs" | ForEach-Object {
        if (Test-Path "$($_.FullName)\env.yaml") {
            Write-Host ("  " + $_.Name) -ForegroundColor Cyan
            & $sdt env resolve $_.Name
            if ($LASTEXITCODE -ne 0) {
                Write-Host ("  WARN: {0} failed to build - it will retry on first tool run" -f $_.Name) -ForegroundColor Yellow
            }
        }
    }
}

Write-Host ""
Write-Host "Done. Open a NEW PowerShell window, then:" -ForegroundColor Green
Write-Host "  sdtools list"
Write-Host "  sdtools mobile-data-preprocessing --data-root <dataset> --epsg <n> --plan"
