param(
    # Defaults to this script's own folder (the bundled PointCONV_TF1_Workflow).
    [string]$WorkflowRoot = $PSScriptRoot,
    # Host data root mounted at /data. No portable default — pass the path for
    # this machine.
    [Parameter(Mandatory=$true)]
    [string]$DataRoot,
    [string]$RunName = "tf1_pointconv_0p1m_tiled_finetune_$(Get-Date -Format 'yyyyMMdd_HHmmss')",
    [string]$DockerImage = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1",
    [string]$PullPolicy = "never",
    # Where the fine-tuned model lives on the host (mounted as /model).
    # The model folder must contain a subdirectory whose name matches
    # PointConv.model_directory inside the input config (default
    # "PointCONV_model_6class_Mobile_v0.0.10"). If that subdirectory is
    # missing, the TF1 wrapper will download it from
    #   s3://sdai-model/lidar_ml/PointCONV_model_6class_Mobile_v0.0.10
    # using the AWS credentials at $HOME/.aws.
    [string]$ModelFolder = (Join-Path $PSScriptRoot 'models'),
    [string]$ModelDirName = "PointCONV_model_6class_Mobile_v0.0.10",
    [string]$AwsCredentialsDir = "$HOME\.aws",
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
    [switch]$SkipPostprocess,
    [string]$InputConfig = "/workspace/tf1/inputconfig_finetune.yml"
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

$resolvedRepo = (Resolve-Path -LiteralPath $WorkflowRoot).Path
$resolvedData = (Resolve-Path -LiteralPath $DataRoot).Path
$resolvedModel = (Resolve-Path -LiteralPath $ModelFolder).Path

# AWS credentials are optional; only required if the model dir is missing
# locally and the wrapper has to download from S3.
$awsMount = $null
if (Test-Path -LiteralPath $AwsCredentialsDir) {
    $awsMount = (Resolve-Path -LiteralPath $AwsCredentialsDir).Path
}

$containerInputDir = "/data/DTECH_2025/ClassifiedLAS"
$containerRunRoot = "/data/DTECH_2025_experiments/$RunName"
$containerTileDir = "$containerRunRoot/preprocessed_tiles"
$containerTf1Output = "$containerRunRoot/tf1_outputs"
$containerCombinedOutput = "$containerRunRoot/combined_outputs"
$containerManifest = "$containerRunRoot/manifests/tf1_tile_manifest.json"

$hostRunRoot = Join-Path $resolvedData "DTECH_2025_experiments\$RunName"
New-Item -ItemType Directory -Force -Path $hostRunRoot | Out-Null

# Verify the model dir locally; if missing we still proceed because the wrapper
# can download it from S3 inside the container.
$localModelPath = Join-Path $resolvedModel $ModelDirName
if (-not (Test-Path -LiteralPath $localModelPath)) {
    Write-Warning ("Model directory not found locally: " + $localModelPath)
    Write-Warning ("Container will attempt to download it from " +
        "s3://sdai-model/lidar_ml/$ModelDirName using AWS credentials at $AwsCredentialsDir.")
    if (-not $awsMount) {
        Write-Warning "AWS credentials directory not found; the S3 download will fail without it."
    }
}

$commonMounts = @(
    "--rm",
    "--pull=$PullPolicy",
    "-v", "${resolvedRepo}:/workspace",
    "-v", "${resolvedData}:/data",
    "-v", "${resolvedModel}:/model"
)
if ($awsMount) {
    $commonMounts += @("-v", "${awsMount}:/root/.aws:ro")
}

Write-Host "Run root:    $hostRunRoot"
Write-Host "Model mount: ${resolvedModel}  (model dir: $ModelDirName)"
Write-Host "Inputconfig: $InputConfig"

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
    if ($Overwrite) { $preArgs += "--overwrite" }
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
        "-v", "${resolvedModel}:/model"
    )
    if ($awsMount) {
        $inferArgs += @("-v", "${awsMount}:/root/.aws:ro")
    }
    $inferArgs += @(
        "-w", "/workspace/tf1",
        $DockerImage,
        "python", "classification.py",
        "--input_inputconfig", $InputConfig,
        "--input_folder", $containerTileDir,
        "--out_folder", $containerTf1Output,
        "--model_folder", "/model"
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
    if ($Overwrite) { $postArgs += "--overwrite" }
    Invoke-Checked -Arguments $postArgs
}

Write-Host ""
Write-Host "Completed run: $hostRunRoot"
Write-Host "Combined LAS outputs: $(Join-Path $hostRunRoot 'combined_outputs')"
