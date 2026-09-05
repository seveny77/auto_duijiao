param(
    [string]$Root = "C:\Autofocus",
    [Parameter(Mandatory)]
    [string]$SegmentationModel,
    [Parameter(Mandatory)]
    [string]$CircleModel
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$rootPath = [IO.Path]::GetFullPath($Root)
$condaExe = Join-Path $rootPath "runtime\miniconda3\Scripts\conda.exe"
$environmentPath = Join-Path $rootPath "runtime\venvs\autofocus-py312"
$pythonExe = Join-Path $environmentPath "python.exe"
$runtimeRequirements = Join-Path $rootPath "deploy\requirements-runtime.txt"
$torchRequirements = Join-Path $rootPath "deploy\requirements-torch-cu128.txt"
$smokeScript = Join-Path $rootPath "tools\smoke_yolo_gpu.py"
$launcherPath = Join-Path $rootPath "deploy\start_gui_py312.cmd"

foreach ($requiredPath in @(
    $condaExe,
    $runtimeRequirements,
    $torchRequirements,
    $smokeScript,
    [IO.Path]::GetFullPath($SegmentationModel),
    [IO.Path]::GetFullPath($CircleModel)
)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file does not exist: $requiredPath"
    }
}

$venvsRoot = [IO.Path]::GetFullPath(
    (Join-Path $rootPath "runtime\venvs")
).TrimEnd('\')
$resolvedEnvironment = [IO.Path]::GetFullPath($environmentPath)
if (-not $resolvedEnvironment.StartsWith(
    $venvsRoot + '\',
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to create environment outside runtime\venvs: $resolvedEnvironment"
}

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    Write-Output "Creating side-by-side Python 3.12 environment: $environmentPath"
    & $condaExe create --yes --prefix $environmentPath "python=3.12" pip
    if ($LASTEXITCODE -ne 0) {
        throw "Conda failed to create the Python 3.12 environment"
    }
}

$version = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne "3.12") {
    throw "Candidate environment is not Python 3.12: $version"
}

Write-Output "Installing pinned PyTorch CUDA packages"
& $pythonExe -m pip install --disable-pip-version-check -r $torchRequirements
if ($LASTEXITCODE -ne 0) {
    throw "PyTorch CUDA package installation failed"
}

Write-Output "Installing application runtime packages"
& $pythonExe -m pip install --disable-pip-version-check -r $runtimeRequirements
if ($LASTEXITCODE -ne 0) {
    throw "Application package installation failed"
}

Write-Output "Running real-model CUDA smoke test"
& $pythonExe $smokeScript `
    --segmentation-model ([IO.Path]::GetFullPath($SegmentationModel)) `
    --circle-model ([IO.Path]::GetFullPath($CircleModel)) `
    --device 0
if ($LASTEXITCODE -ne 0) {
    throw "CUDA smoke test failed; the existing Python 3.13 environment was not changed"
}

if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "Python 3.12 GUI launcher is missing: $launcherPath"
}

Write-Output "PYTHON312_CUDA_READY"
Write-Output "Start the GUI with: $launcherPath"
