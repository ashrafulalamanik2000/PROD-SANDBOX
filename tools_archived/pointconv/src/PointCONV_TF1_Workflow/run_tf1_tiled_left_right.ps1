param(
    [Parameter(Mandatory=$true)]
    [string]$InputDir,                       # folder containing 2+ raw LAS files of the same scene

    [Parameter(Mandatory=$true)]
    [string]$RunRoot,                        # parent folder where the timestamped run dir will be created

    # Defaults to this script's own folder (the bundled PointCONV_TF1_Workflow).
    [string]$WorkflowRoot = $PSScriptRoot,
    # Parent folder for experiment outputs. No portable default — pass one.
    [Parameter(Mandatory=$true)]
    [string]$ExperimentsRoot,

    # Directory containing the inputs (e.g. DTECH/Mississauga/Oakville etc.) so the
    # bind-mount under /data resolves any of them. No portable default — pass one.
    [Parameter(Mandatory=$true)]
    [string]$DataRoot,

    [string]$RunName = "inference_lr_$(Get-Date -Format 'yyyyMMdd_HHmmss')",
    [string]$CombinedSourceName = "combined",  # name of the unified source (no extension)

    [string]$DockerImage = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1",
    [string]$ModelDirName = "PointCONV_model_6class_Mobile_v0.0.10",
    [string]$ModelFolder = (Join-Path $PSScriptRoot 'models'),
    [string]$AwsCredentialsDir = "$HOME\.aws",

    [double]$VoxelSize = 0.1,
    [int]$TargetTilePoints = 400000,
    [int]$MinTilePoints = 25000,
    [double]$MinRadius = 20.0,
    [double]$Overlap = 20.0,
    [int]$PreprocessWorkers = 2,
    [int]$PostprocessWorkers = 2,
    [string]$InputConfig = "/workspace/tf1/inputconfig_finetune.yml",

    [switch]$Overwrite,
    [switch]$SkipCombine,
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
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed with exit code $LASTEXITCODE" }
}

# --- Resolve host paths & build container paths --------------------------------

$inputDirHost   = (Resolve-Path -LiteralPath $InputDir).Path
$workflowHost   = (Resolve-Path -LiteralPath $WorkflowRoot).Path
$experimentsHost = (Resolve-Path -LiteralPath $ExperimentsRoot).Path
$dataHost       = (Resolve-Path -LiteralPath $DataRoot).Path
$modelHost      = (Resolve-Path -LiteralPath $ModelFolder).Path
$awsHost        = $null
if (Test-Path -LiteralPath $AwsCredentialsDir) { $awsHost = (Resolve-Path -LiteralPath $AwsCredentialsDir).Path }

# The input directory must live somewhere under DataRoot so the same /data mount sees it.
$dataRootResolved = $dataHost.TrimEnd('\','/')
if (-not $inputDirHost.StartsWith($dataRootResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "InputDir '$InputDir' is not under DataRoot '$DataRoot'. Either move the inputs or pass a -DataRoot that contains them."
}
$inputContainer = "/data/" + ($inputDirHost.Substring($dataRootResolved.Length).TrimStart('\','/').Replace('\','/'))

$runRoot     = Join-Path $experimentsHost $RunName
$runContainer = "/exp/$RunName"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

$combinedSourceDirHost      = Join-Path $runRoot "combined_source"
$combinedSourceDirContainer = "$runContainer/combined_source"
New-Item -ItemType Directory -Force -Path $combinedSourceDirHost | Out-Null

$combinedLasHost      = Join-Path $combinedSourceDirHost   "${CombinedSourceName}.las"
$combinedLasContainer = "$combinedSourceDirContainer/${CombinedSourceName}.las"

$tilesContainer    = "$runContainer/preprocessed_tiles"
$tf1OutContainer   = "$runContainer/tf1_outputs"
$mergeOutContainer = "$runContainer/combined_outputs"
$manifestContainer = "$runContainer/manifests/tf1_tile_manifest.json"

# Render the inputconfig into the run dir so the file is colocated with its outputs.
Copy-Item -LiteralPath (Join-Path $workflowHost "tf1\inputconfig_finetune.yml") -Destination (Join-Path $runRoot "inputconfig_finetune.yml") -Force | Out-Null

$commonMounts = @(
    "--rm",
    "--pull=never",
    "-v", "${workflowHost}:/workspace",
    "-v", "${dataHost}:/data",
    "-v", "${experimentsHost}:/exp",
    "-v", "${modelHost}:/model"
)
if ($awsHost) { $commonMounts += @("-v", "${awsHost}:/root/.aws:ro") }

Write-Host "Run root:        $runRoot"
Write-Host "Input dir:       $inputDirHost  -> $inputContainer"
Write-Host "Combined LAS:    $combinedLasHost"
Write-Host "Model:           $modelHost\$ModelDirName  (-> /model/$ModelDirName)"
Write-Host "Inputconfig:     $InputConfig"

# --- Stage 1: combine + thin -------------------------------------------------

if (-not $SkipCombine) {
    if ((Test-Path -LiteralPath $combinedLasHost) -and (-not $Overwrite)) {
        Write-Host "Combined LAS already exists, skipping (-Overwrite to force)"
    } else {
        # List input LAS files inside the container path so the script sees them.
        $lasFiles = Get-ChildItem -LiteralPath $inputDirHost -File -Filter *.las
        if ($lasFiles.Count -lt 2) {
            throw "Need at least 2 .las files in $inputDirHost, found $($lasFiles.Count)"
        }
        $inputArgs = @()
        foreach ($f in $lasFiles) {
            $rel = $f.FullName.Substring($dataRootResolved.Length).TrimStart('\','/').Replace('\','/')
            $inputArgs += "/data/$rel"
        }

        $combineArgs = @(
            $commonMounts
            "-w", "/workspace",
            $DockerImage,
            "python", "/workspace/tools/combine_thinned_las.py",
            "--inputs"
        ) + $inputArgs + @(
            "--output", $combinedLasContainer,
            "--voxel-size", $VoxelSize.ToString([System.Globalization.CultureInfo]::InvariantCulture),
            "--no-laz"
        )
        Invoke-Checked -Arguments $combineArgs
    }
}

# --- Stage 2: tile builder (treats combined LAS as the single source) --------

if (-not $SkipPreprocess) {
    $preArgs = @(
        $commonMounts
        "-w", "/workspace",
        $DockerImage,
        "python", "/workspace/pre_processing/build_tf1_inference_tiles.py",
        "--input-dir", $combinedSourceDirContainer,
        "--output-root", $runContainer,
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

# --- Stage 3: TF1 inference --------------------------------------------------

if (-not $SkipInference) {
    $inferArgs = @(
        "--rm", "--pull=never",
        "--gpus", "all", "--shm-size=8gb",
        "-v", "${workflowHost}:/workspace",
        "-v", "${dataHost}:/data",
        "-v", "${experimentsHost}:/exp",
        "-v", "${modelHost}:/model"
    )
    if ($awsHost) { $inferArgs += @("-v", "${awsHost}:/root/.aws:ro") }
    $inferArgs += @(
        "-w", "/workspace/tf1",
        $DockerImage,
        "python", "classification.py",
        "--input_inputconfig", $InputConfig,
        "--input_folder", $tilesContainer,
        "--out_folder", $tf1OutContainer,
        "--model_folder", "/model"
    )
    Invoke-Checked -Arguments $inferArgs
}

# --- Stage 4: merge tiles ----------------------------------------------------

if (-not $SkipPostprocess) {
    $postArgs = @(
        $commonMounts
        "-w", "/workspace",
        $DockerImage,
        "python", "/workspace/post_processing/merge_tf1_tile_predictions.py",
        "--manifest", $manifestContainer,
        "--tf1-output-root", $tf1OutContainer,
        "--output-dir", $mergeOutContainer,
        "--workers", $PostprocessWorkers.ToString()
    )
    if ($Overwrite) { $postArgs += "--overwrite" }
    Invoke-Checked -Arguments $postArgs
}

Write-Host ""
Write-Host "Completed run: $runRoot"
Write-Host "Combined LAS prediction: $(Join-Path $runRoot 'combined_outputs')\${CombinedSourceName}_tf1_pointconv_combined_0p1m.las"
