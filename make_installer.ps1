# Build the one-click sdtools installer (and companion zip) for distribution.
#
#   powershell -ExecutionPolicy Bypass -File .\make_installer.ps1
#
# Needs Inno Setup 6 (winget install -e --id JRSoftware.InnoSetup).
# Output: C:\sdt_out\sdtools-setup-<date>.exe + sdtools-<date>.zip
#
# Why the short C:\sdt_stage staging dir: the pointconv model path is ~140
# chars, and both Compress-Archive and Explorer extraction die past MAX_PATH.
# Why not tar.exe for the zip: bsdtar writes ./-prefixed entries that Windows
# Explorer and Expand-Archive reject as "invalid".

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$stage = "C:\sdt_stage"
$out = "C:\sdt_out"
$date = Get-Date -Format "yyyy-MM-dd"

# --- stage (exclude built envs, caches, and the obsolete mock tool) --------
if (Test-Path $stage) { cmd /c "rmdir /s /q $stage" }
New-Item -ItemType Directory -Force $stage | Out-Null
foreach ($d in @("sdtools", "api", "dashboard", "docs", "tests")) {
    robocopy "$root\$d" "$stage\$d" /E /XD __pycache__ .pytest_cache /NFL /NDL /NP /NJH /NJS /R:1 /W:1 | Out-Null
}
robocopy "$root\tools" "$stage\tools" /E /XD __pycache__ aecon_process /NFL /NDL /NP /NJH /NJS /R:1 /W:1 | Out-Null
Get-ChildItem -Directory "$root\envs" | Where-Object { $_.Name -ne "aecon_process" } | ForEach-Object {
    New-Item -ItemType Directory -Force "$stage\envs\$($_.Name)" | Out-Null
    Get-ChildItem $_.FullName -File |
        Where-Object { $_.Name -in @("env.yaml", "pixi.toml", "pixi.lock", "requirements.lock") } |
        Copy-Item -Destination "$stage\envs\$($_.Name)\"
}
foreach ($f in @("install.ps1", "update.ps1", "make_installer.ps1", "installer.iss", "README.md",
                 "ARCHITECTURE.md", "pyproject.toml", "config.toml", "config.example.toml",
                 "docker-compose.yml", ".gitignore")) {
    if (Test-Path "$root\$f") { Copy-Item "$root\$f" "$stage\" }
}

# --- zip (companion; Explorer-compatible) ----------------------------------
New-Item -ItemType Directory -Force $out | Out-Null
$zip = "$out\sdtools-$date.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\*" -DestinationPath $zip -CompressionLevel Optimal

# --- exe --------------------------------------------------------------------
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { Write-Host "Inno Setup 6 not found - winget install -e --id JRSoftware.InnoSetup" -ForegroundColor Red; exit 1 }
& $iscc "$root\installer.iss" /Q ("/DAppVer=" + $date) ("/DOutDir=" + $out)
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Get-ChildItem $out | Select-Object Name, @{n='MB';e={[Math]::Round($_.Length/1MB,1)}}
Write-Host ""
Write-Host "Publish both files to \\SDAI-FS1\Production\Projects\CLAUDE\SDAI-PROD-AGENT-01\sdtools_dist\" -ForegroundColor Green
