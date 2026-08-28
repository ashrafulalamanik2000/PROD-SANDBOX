param(
    # Defaults to the parent of this script's folder (the bundled PointCONV_TF1_Workflow).
    [string]$WorkflowRoot = (Split-Path -Parent $PSScriptRoot),
    # Experiment + data roots have no portable default — pass the paths for this machine.
    [Parameter(Mandatory=$true)]
    [string]$ExperimentsRoot,
    [Parameter(Mandatory=$true)]
    [string]$DataRoot,
    [string]$RunName = "finetune_20260429_125114",
    [string]$DockerImage = "750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:v1.8.0.1",
    [int]$Epochs = 0,
    [switch]$SmokeTest,
    [int]$MaxTrainRegions = 0,
    [int]$MaxValRegions = 0
)

$ErrorActionPreference = "Stop"

$workflow = (Resolve-Path -LiteralPath $WorkflowRoot).Path
$experiments = (Resolve-Path -LiteralPath $ExperimentsRoot).Path
$data = (Resolve-Path -LiteralPath $DataRoot).Path

$runRoot = Join-Path $experiments $RunName
if (-not (Test-Path -LiteralPath $runRoot)) {
    throw "Run dir not found: $runRoot. Run prepare_finetune_data.py first."
}

$dockerArgs = @(
    "--rm",
    "--pull=never",
    "--gpus", "all",
    "--shm-size=8gb",
    "-v", "${workflow}:/workspace",
    "-v", "${experiments}:/exp",
    "-v", "${data}:/data",
    "-w", "/workspace",
    $DockerImage,
    "python", "/workspace/finetune/train_finetune.py",
    "--config", "/workspace/finetune/finetune_config.yml",
    "--data-root", "/exp/$RunName/data",
    "--model-out", "/exp/$RunName/model",
    "--log-dir", "/exp/$RunName/logs/train"
)

if ($SmokeTest) { $dockerArgs += "--smoke-test" }
if ($Epochs -gt 0) { $dockerArgs += @("--epochs", $Epochs.ToString()) }
if ($MaxTrainRegions -gt 0) { $dockerArgs += @("--max-train-regions", $MaxTrainRegions.ToString()) }
if ($MaxValRegions -gt 0) { $dockerArgs += @("--max-val-regions", $MaxValRegions.ToString()) }

Write-Host ("docker run " + ($dockerArgs -join " "))
& docker run @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Training docker run failed with exit code $LASTEXITCODE"
}
