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
    [int]$LoginTimeoutSeconds = 300,
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

$resolvedOutput = Resolve-OutputPath $Output
$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$stdoutPath = "$resolvedOutput.stdout.log"
$stderrPath = "$resolvedOutput.stderr.log"
$markerPath = "$resolvedOutput.launcher.json"
$progressPath = "$resolvedOutput.progress.json"

$startedAt = (Get-Date).ToString("o")
$resolvedProjectRoot = ""
$resolvedPayload = ""
$resolvedSharedRuntimeRoot = ""
$inspectorPath = ""
$resolvedPython = ""
$pythonArguments = @()
$exitCode = $null
$launcherError = ""
$stage = "launcher_started"
$childStarted = $false
$terminal = $false

function Write-LauncherMarker([string]$Status) {
    $marker = [ordered]@{
        artifact_version = "erp_saved_draft_inspector_launcher_v2"
        status = $Status
        stage = $stage
        started_at = $startedAt
        updated_at = (Get-Date).ToString("o")
        finished_at = if ($terminal) { (Get-Date).ToString("o") } else { $null }
        launcher_pid = $PID
        session_id = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
        principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        user_interactive = [Environment]::UserInteractive
        project_root = $resolvedProjectRoot
        requested_project_root = $ProjectRoot
        python_exe = $resolvedPython
        inspector = $inspectorPath
        arguments = $pythonArguments
        child_started = $childStarted
        exit_code = $exitCode
        launcher_error = $launcherError
        output = $resolvedOutput
        output_exists = Test-Path -LiteralPath $resolvedOutput -PathType Leaf
        progress = $progressPath
        progress_exists = Test-Path -LiteralPath $progressPath -PathType Leaf
        stdout = $stdoutPath
        stderr = $stderrPath
        draft_saved = $false
        offer_submitted = $false
    }
    $renderedMarker = $marker | ConvertTo-Json -Depth 6
    $temporaryMarkerPath = "$markerPath.$PID.tmp"
    [System.IO.File]::WriteAllText(
        $temporaryMarkerPath,
        $renderedMarker + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    if ([System.IO.File]::Exists($markerPath)) {
        $backupMarkerPath = "$markerPath.$PID.bak"
        [System.IO.File]::Replace(
            $temporaryMarkerPath,
            $markerPath,
            $backupMarkerPath,
            $true
        )
        [System.IO.File]::Delete($backupMarkerPath)
    } else {
        [System.IO.File]::Move($temporaryMarkerPath, $markerPath)
    }
}

Write-LauncherMarker "running"

try {
    $stage = "preflight"
    if ($LoginTimeoutSeconds -le 0) {
        throw "LoginTimeoutSeconds must be greater than zero."
    }
    $resolvedProjectRoot = Resolve-RequiredDirectory $ProjectRoot "ProjectRoot"
    $resolvedPayload = Resolve-RequiredFile $Payload "Payload"
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

    $pythonArguments = @(
        "-u",
        "-X", "utf8",
        $inspectorPath,
        "--payload", $resolvedPayload,
        "--output", $resolvedOutput,
        "--progress-output", $progressPath,
        "--draft-id", $DraftId,
        "--expected-shop", $ExpectedShop,
        "--account-key", $AccountKey,
        "--expected-cdp-port", ([string]$ExpectedCdpPort),
        "--shared-runtime-root", $resolvedSharedRuntimeRoot,
        "--lock-wait-seconds", ([string]$LockWaitSeconds),
        "--runtime-lease-wait-seconds", $RuntimeLeaseWaitSeconds.ToString(
            [System.Globalization.CultureInfo]::InvariantCulture
        ),
        "--login-timeout-seconds", ([string]$LoginTimeoutSeconds)
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

    $stage = "preflight_complete"
    Write-LauncherMarker "running"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONUNBUFFERED = "1"
    Push-Location $resolvedProjectRoot
    try {
        $stage = "inspector_running"
        $childStarted = $true
        Write-LauncherMarker "running"
        # Windows PowerShell 5.1 promotes native stderr to ErrorRecord objects.
        # Keep those diagnostics in the stderr artifact without terminating the
        # wrapper before the native process has returned its real exit code.
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $resolvedPython @pythonArguments 1> $stdoutPath 2> $stderrPath
            $nativeExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($null -eq $nativeExitCode) {
            throw "Inspector process exited without an exit code."
        }
        $exitCode = [int]$nativeExitCode
        $stage = "inspector_exited"
    } finally {
        Pop-Location
    }
} catch {
    $launcherError = $_.Exception.Message
    if ($null -eq $exitCode) {
        $exitCode = 1
    }
    $stage = if ($childStarted) { "launcher_failed" } else { "preflight_failed" }
    [System.IO.File]::AppendAllText(
        $stderrPath,
        "LAUNCHER_ERROR: " + $launcherError + [Environment]::NewLine
    )
} finally {
    $terminal = $true
    $status = if ($launcherError -or [int]$exitCode -ne 0) { "failed" } else { "completed" }
    Write-LauncherMarker $status
}

if ($launcherError) {
    [Console]::Error.WriteLine($launcherError)
}
exit $exitCode
