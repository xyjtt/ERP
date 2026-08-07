[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$Payload,
    [Parameter(Mandatory = $true)]
    [string]$Output,
    [Parameter(Mandatory = $true)]
    [string]$DraftId,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedShop,
    [string]$AccountKey = "muke_lixiang",
    [int]$ExpectedCdpPort = 9306,
    [string]$SharedRuntimeRoot = "D:\script_1688",
    [string]$OfferId = "",
    [string]$OfferUrl = "",
    [string]$PublishUrl = "",
    [int]$LockWaitSeconds = 0,
    [double]$RuntimeLeaseWaitSeconds = 0,
    [string]$PythonExe = "",
    [switch]$OpenFromManagement
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-RequiredFile([string]$PathValue, [string]$Label) {
    $resolved = Resolve-Path -LiteralPath $PathValue -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $resolved.Path -PathType Leaf)) {
        throw "$Label is not a file: $PathValue"
    }
    return $resolved.Path
}

function Resolve-RequiredDirectory([string]$PathValue, [string]$Label) {
    $resolved = Resolve-Path -LiteralPath $PathValue -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $resolved.Path -PathType Container)) {
        throw "$Label is not a directory: $PathValue"
    }
    return $resolved.Path
}

function Resolve-OutputPath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $PathValue))
}

$resolvedProjectRoot = Resolve-RequiredDirectory $ProjectRoot "ProjectRoot"
$resolvedPayload = Resolve-RequiredFile $Payload "Payload"
$resolvedOutput = Resolve-OutputPath $Output
$resolvedSharedRuntimeRoot = Resolve-RequiredDirectory $SharedRuntimeRoot "SharedRuntimeRoot"
$inspectorPath = Resolve-RequiredFile (
    Join-Path $resolvedProjectRoot "scripts\inspect_1688_saved_draft.py"
) "Inspector"

if ($PythonExe) {
    $resolvedPython = Resolve-RequiredFile $PythonExe "PythonExe"
} else {
    $venvPython = Join-Path $resolvedProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $resolvedPython = (Resolve-Path -LiteralPath $venvPython).Path
    } else {
        $pythonCommand = Get-Command python.exe -ErrorAction Stop
        $resolvedPython = $pythonCommand.Source
    }
}

$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$stdoutPath = "$resolvedOutput.stdout.log"
$stderrPath = "$resolvedOutput.stderr.log"
$markerPath = "$resolvedOutput.launcher.json"

$pythonArguments = @(
    "-X", "utf8",
    $inspectorPath,
    "--payload", $resolvedPayload,
    "--output", $resolvedOutput,
    "--draft-id", $DraftId,
    "--expected-shop", $ExpectedShop,
    "--account-key", $AccountKey,
    "--expected-cdp-port", ([string]$ExpectedCdpPort),
    "--shared-runtime-root", $resolvedSharedRuntimeRoot,
    "--lock-wait-seconds", ([string]$LockWaitSeconds),
    "--runtime-lease-wait-seconds", $RuntimeLeaseWaitSeconds.ToString(
        [System.Globalization.CultureInfo]::InvariantCulture
    )
)
if ($OpenFromManagement) {
    $pythonArguments += "--open-from-management"
}
if ($PublishUrl) {
    $pythonArguments += @("--publish-url", $PublishUrl)
}
if ($OfferId) {
    $pythonArguments += @("--offer-id", $OfferId)
}
if ($OfferUrl) {
    $pythonArguments += @("--offer-url", $OfferUrl)
}

$startedAt = (Get-Date).ToString("o")
$exitCode = 1
$launcherError = ""
try {
    $env:PYTHONUTF8 = "1"
    Push-Location $resolvedProjectRoot
    try {
        & $resolvedPython @pythonArguments 1> $stdoutPath 2> $stderrPath
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} catch {
    $launcherError = $_.Exception.Message
    [System.IO.File]::WriteAllText($stderrPath, $launcherError + [Environment]::NewLine)
} finally {
    $marker = [ordered]@{
        artifact_version = "erp_saved_draft_inspector_launcher_v1"
        started_at = $startedAt
        finished_at = (Get-Date).ToString("o")
        project_root = $resolvedProjectRoot
        python_exe = $resolvedPython
        inspector = $inspectorPath
        arguments = $pythonArguments
        exit_code = $exitCode
        launcher_error = $launcherError
        output = $resolvedOutput
        output_exists = Test-Path -LiteralPath $resolvedOutput -PathType Leaf
        stdout = $stdoutPath
        stderr = $stderrPath
        draft_saved = $false
        offer_submitted = $false
    }
    $renderedMarker = $marker | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText(
        $markerPath,
        $renderedMarker + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

if ($launcherError) {
    [Console]::Error.WriteLine($launcherError)
}
exit $exitCode
