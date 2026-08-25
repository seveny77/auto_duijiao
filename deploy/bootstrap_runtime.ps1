param(
    [string]$Root = "C:\Autofocus",
    [switch]$SkipGit
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$deployDir = Join-Path $Root "deploy"
$runtimeDir = Join-Path $Root "runtime"
$gitRoot = Join-Path $runtimeDir "mingit"
$condaRoot = Join-Path $runtimeDir "miniconda3"

New-Item -ItemType Directory -Force -Path $deployDir, $runtimeDir | Out-Null

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory)]
        [string]$Uri,

        [Parameter(Mandatory)]
        [string]$Destination,

        [Parameter(Mandatory)]
        [string]$ExpectedSha256
    )

    if (Test-Path -LiteralPath $Destination) {
        $existingHash = (
            Get-FileHash -LiteralPath $Destination -Algorithm SHA256
        ).Hash

        if ($existingHash -eq $ExpectedSha256) {
            Write-Output "Verified existing file: $Destination"
            return
        }

        Write-Output "Removing incomplete or invalid file: $Destination"
        Remove-Item -LiteralPath $Destination -Force
    }

    $partialPath = "$Destination.part"

    Write-Output "Downloading: $Uri"

    $curlArgs = @(
        "--fail"
        "--location"
        "--retry", "8"
        "--retry-delay", "3"
        "--connect-timeout", "20"
    )

    if (Test-Path -LiteralPath $partialPath) {
        Write-Output "Resuming partial download: $partialPath"
        $curlArgs += @("--continue-at", "-")
    }

    $curlArgs += @("--output", $partialPath, $Uri)
    & "$env:SystemRoot\System32\curl.exe" @curlArgs

    if ($LASTEXITCODE -ne 0) {
        throw "Download failed with curl exit code ${LASTEXITCODE}: $Uri"
    }

    $actualHash = (
        Get-FileHash -LiteralPath $partialPath -Algorithm SHA256
    ).Hash

    if ($actualHash -ne $ExpectedSha256) {
        Remove-Item -LiteralPath $partialPath -Force
        throw "SHA-256 mismatch: $Uri"
    }

    Move-Item -LiteralPath $partialPath -Destination $Destination
    Write-Output "Verified download: $Destination"
}

$gitArchive = Join-Path $deployDir "MinGit-2.55.0.5-64-bit.zip"
$condaInstaller = Join-Path $deployDir (
    "Miniconda3-py313_26.3.2-2-Windows-x86_64.exe"
)

Get-VerifiedDownload `
    -Uri "https://repo.anaconda.com/miniconda/Miniconda3-py313_26.3.2-2-Windows-x86_64.exe" `
    -Destination $condaInstaller `
    -ExpectedSha256 "FE980247DFD30AF229A55D9505B57E7C8DFBDB9D24C5BC66FB6078B6A2D53414"

if (-not $SkipGit) {
    Get-VerifiedDownload `
        -Uri "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/MinGit-2.55.0.5-64-bit.zip" `
        -Destination $gitArchive `
        -ExpectedSha256 "56D7B226B7693196CFC71FEF26568F536C4A021AB6C37FF2DB4287BED908E96E"

    if (-not (Test-Path -LiteralPath (Join-Path $gitRoot "cmd\git.exe"))) {
        if (Test-Path -LiteralPath $gitRoot) {
            throw "MinGit target exists but is incomplete: $gitRoot"
        }

        Write-Output "Extracting MinGit to $gitRoot"
        Expand-Archive -LiteralPath $gitArchive -DestinationPath $gitRoot
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $condaRoot "Scripts\conda.exe"))) {
    if (Test-Path -LiteralPath $condaRoot) {
        throw "Miniconda target exists but is incomplete: $condaRoot"
    }

    Write-Output "Installing Miniconda to $condaRoot"
    & $condaInstaller `
        /InstallationType=JustMe `
        /RegisterPython=0 `
        /AddToPath=0 `
        /S `
        "/D=$condaRoot"

    if ($LASTEXITCODE -ne 0) {
        throw "Miniconda installer failed with exit code $LASTEXITCODE"
    }
}

$condaExe = Join-Path $condaRoot "Scripts\conda.exe"

if (-not $SkipGit) {
    $gitExe = Join-Path $gitRoot "cmd\git.exe"
    Write-Output "Git:"
    & $gitExe --version
}

Write-Output "Conda:"
& $condaExe --version

Write-Output "BOOTSTRAP_OK"
