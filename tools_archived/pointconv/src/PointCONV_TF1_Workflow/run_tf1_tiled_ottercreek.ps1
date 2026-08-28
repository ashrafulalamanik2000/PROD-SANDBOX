param(
    # Defaults to this script's own folder (the bundled PointCONV_TF1_Workflow).
    [string]$RepoRoot = $PSScriptRoot,
    # Host data root mounted at /data (must contain DTECH_2025/ClassifiedLAS). No
    # portable default — pass the path for this machine.
    [Parameter(Mandatory=$true)]
    [string]$DataRoot,
    [string]$RunName = "tf1_pointconv_0p1m_tiled_$(Get-Date -Format 'yyyyMMdd_HHmmss')",
    [string]$DockerImage = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1",
    [string]$PullPolicy = "never",
    [int]$PreprocessWorkers = 2,
    [int]$PostprocessWorkers = 2,
    [double]$VoxelSize = 0.1,
    [int]$TargetTilePoints = 400000,
    [int]$MinTilePoints = 25000,
    [double]$MinRadius = 20.0,
    [double]$Overlap = 20.0,
    [switch]$Overwrite,
    [switch]$SkipPreprocess,
    [switch]$SkipInference,
    [switch]$SkipPostprocess
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([string[]]$Arguments)
    Write-Host ""
    Write-Host ("docker run " + ($Arguments -join " "))
    & docker run @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE"
    }
}

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
$resolvedData = (Resolve-Path -LiteralPath $DataRoot).Path

$containerInputDir = "/data/DTECH_2025/ClassifiedLAS"
$containerRunRoot = "/data/DTECH_2025_experiments/$RunName"
$containerTileDir = "$containerRunRoot/preprocessed_tiles"
$containerTf1Output = "$containerRunRoot/tf1_outputs"
$containerCombinedOutput = "$containerRunRoot/combined_outputs"
$containerManifest = "$containerRunRoot/manifests/tf1_tile_manifest.json"

$hostRunRoot = Join-Path $resolvedData "DTECH_2025_experiments\$RunName"
New-Item -ItemType Directory -Force -Path $hostRunRoot | Out-Null

$commonMounts = @(
    "--rm",
    "--pull=$PullPolicy",
    "-v", "${resolvedRepo}:/workspace",
    "-v", "${resolvedData}:/data"
)

Write-Host "Run root: $hostRunRoot"

if (-not $SkipPreprocess) {
    $preArgs = @(
        $commonMounts
        "-w", "/workspace",
        $DockerImage,
        "python", "/workspace/pre_processing/build_tf1_inference_tiles.py",
        "--input-dir", $containerInputDir,
        "--output-root", $containerRunRoot,
        "--voxel-size", $VoxelSize.ToString([System.Globalization.CultureInfo]::InvariantCulture),
        "--target-tile-points", $TargetTilePoints.ToString(),
        "--min-tile-points", $MinTilePoints.ToString(),
        "--min-radius", $MinRadius.ToString([System.Globalization.CultureInfo]::InvariantCulture),
        "--overlap", $Overlap.ToString([System.Globalization.CultureInfo]::InvariantCulture),
        "--workers", $PreprocessWorkers.ToString()
    )
    if ($Overwrite) {
        $preArgs += "--overwrite"
    }
    Invoke-Checked -Arguments $preArgs
}

if (-not $SkipInference) {
    $inferArgs = @(
        "--rm",
        "--pull=$PullPolicy",
        "--gpus", "all",
        "--shm-size=8gb",
        "-v", "${resolvedRepo}:/workspace",
        "-v", "${resolvedData}:/data",
        "-w", "/workspace/tf1",
        $DockerImage,
        "python", "classification.py",
        "--input_inputconfig", "/workspace/tf1/inputconfig.yml",
        "--input_folder", $containerTileDir,
        "--out_folder", $containerTf1Output,
        "--model_folder", "/workspace/Model_Development/safe_smoketest/model"
    )
    Invoke-Checked -Arguments $inferArgs
}

if (-not $SkipPostprocess) {
    $postArgs = @(
        $commonMounts
        "-w", "/workspace",
        $DockerImage,
        "python", "/workspace/post_processing/merge_tf1_tile_predictions.py",
        "--manifest", $containerManifest,
        "--tf1-output-root", $containerTf1Output,
        "--output-dir", $containerCombinedOutput,
        "--workers", $PostprocessWorkers.ToString()
    )
    if ($Overwrite) {
        $postArgs += "--overwrite"
    }
    Invoke-Checked -Arguments $postArgs
}

Write-Host ""
Write-Host "Completed run: $hostRunRoot"
Write-Host "Combined LAS outputs: $(Join-Path $hostRunRoot 'combined_outputs')"
