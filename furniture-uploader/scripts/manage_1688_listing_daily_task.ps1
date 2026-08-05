[CmdletBinding()]
param(
    [ValidateSet("install", "preview", "run", "status", "enable", "disable", "start", "stop", "uninstall")]
    [string]$Action = "status",
    [string]$TaskName = "YYDD-1688-Listing-Daily",
    [string]$DailyAt = "08:00",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SharedRuntimeRoot = "E:\1688\1688-script-new",
    [string]$CandidateRoot = "C:\ProgramData\YYDD\1688-listing\inbox",
    [string]$AccountKey = "muke_lixiang",
    [ValidateRange(1, 500)]
    [int]$MaxItems = 50,
    [ValidateRange(60, 21600)]
    [int]$TaskTimeoutSeconds = 7200
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$SharedRuntimeRoot = [IO.Path]::GetFullPath($SharedRuntimeRoot)
$CandidateRoot = [IO.Path]::GetFullPath($CandidateRoot)

if ($TaskName -ne "YYDD-1688-Listing-Daily") {
    throw "The formal listing task name must be YYDD-1688-Listing-Daily."
}
if ($AccountKey -ne "muke_lixiang") {
    throw "The current formal listing schedule is bound to account_key=muke_lixiang."
}

function Resolve-PythonExecutable {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "python.exe was not found. Install dependencies or create .venv first."
    }
    return $command.Source
}

function Get-ListingTaskStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $latestSummaryPath = Join-Path $ProjectRoot "logs\listing_daily\scheduler\latest.summary.json"
    $latestSummary = $null
    if (Test-Path -LiteralPath $latestSummaryPath) {
        try {
            $latestSummary = Get-Content -LiteralPath $latestSummaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        catch {
            $latestSummary = [ordered]@{ status = "unreadable" }
        }
    }
    if ($null -eq $task) {
        return [ordered]@{
            task_name = $TaskName
            exists = $false
            state = "Missing"
            enabled = $false
            latest_summary_path = $latestSummaryPath
            latest_summary = $latestSummary
        }
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $principal = $task.Principal
    return [ordered]@{
        task_name = $TaskName
        exists = $true
        state = [string]$task.State
        enabled = ([string]$task.State -ne "Disabled")
        last_run_time = $info.LastRunTime
        last_task_result = $info.LastTaskResult
        next_run_time = $info.NextRunTime
        missed_runs = $info.NumberOfMissedRuns
        principal = [ordered]@{
            user_id = [string]$principal.UserId
            logon_type = [string]$principal.LogonType
            run_level = [string]$principal.RunLevel
        }
        actions = @($task.Actions | ForEach-Object {
            [ordered]@{
                execute = [string]$_.Execute
                arguments = [string]$_.Arguments
                working_directory = [string]$_.WorkingDirectory
            }
        })
        triggers = @($task.Triggers | ForEach-Object {
            [ordered]@{
                start_boundary = [string]$_.StartBoundary
                enabled = [bool]$_.Enabled
                days_interval = $_.DaysInterval
            }
        })
        latest_summary_path = $latestSummaryPath
        latest_summary = $latestSummary
    }
}

function Invoke-ListingDaily {
    param(
        [ValidateSet("preview", "execute")]
        [string]$Mode
    )
    $runner = Join-Path $PSScriptRoot "manage_1688_listing_daily.py"
    if (-not (Test-Path -LiteralPath $runner)) {
        throw "Listing daily manager was not found: $runner"
    }
    if (-not (Test-Path -LiteralPath $CandidateRoot -PathType Container)) {
        throw "Listing candidate inbox does not exist: $CandidateRoot"
    }
    $python = Resolve-PythonExecutable
    $logRoot = Join-Path $ProjectRoot "logs\listing_daily\scheduler\scheduled_logs"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $logPath = Join-Path $logRoot ((Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
    $arguments = @(
        $runner,
        "run",
        "--mode", $Mode,
        "--candidate-root", $CandidateRoot,
        "--shared-runtime-root", $SharedRuntimeRoot,
        "--account-key", $AccountKey,
        "--operator", $TaskName,
        "--max-items", [string]$MaxItems,
        "--task-timeout-seconds", [string]$TaskTimeoutSeconds
    )
    if ($Mode -eq "execute") {
        $arguments += "--yes"
    }
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Remove-Item Env:ENABLE_1688_LISTING_SUBMIT -ErrorAction SilentlyContinue
    Push-Location $ProjectRoot
    try {
        & $python @arguments *>> $logPath
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

switch ($Action) {
    "install" {
        [void][datetime]::ParseExact($DailyAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
        if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
            throw "Listing project root does not exist: $ProjectRoot"
        }
        if (-not (Test-Path -LiteralPath $SharedRuntimeRoot -PathType Container)) {
            throw "Shared 1688 runtime root does not exist: $SharedRuntimeRoot"
        }
        if (-not (Test-Path -LiteralPath $CandidateRoot -PathType Container)) {
            throw "Listing candidate inbox does not exist: $CandidateRoot"
        }
        $taskArguments = @(
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", ('"' + $PSCommandPath + '"'),
            "-Action", "run",
            "-TaskName", ('"' + $TaskName + '"'),
            "-ProjectRoot", ('"' + $ProjectRoot + '"'),
            "-SharedRuntimeRoot", ('"' + $SharedRuntimeRoot + '"'),
            "-CandidateRoot", ('"' + $CandidateRoot + '"'),
            "-AccountKey", $AccountKey,
            "-MaxItems", [string]$MaxItems,
            "-TaskTimeoutSeconds", [string]$TaskTimeoutSeconds
        ) -join " "
        $scheduledAction = New-ScheduledTaskAction `
            -Execute "PowerShell.exe" `
            -Argument $taskArguments `
            -WorkingDirectory $ProjectRoot
        $trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Hours 24) `
            -MultipleInstances IgnoreNew `
            -RestartCount 1 `
            -RestartInterval (New-TimeSpan -Minutes 15)
        $principal = New-ScheduledTaskPrincipal `
            -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
            -LogonType Interactive `
            -RunLevel Highest
        $task = New-ScheduledTask `
            -Action $scheduledAction `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description "Daily guarded 1688 listing drafts; approval and submit are excluded"
        Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "preview" {
        exit (Invoke-ListingDaily -Mode preview)
    }
    "run" {
        exit (Invoke-ListingDaily -Mode execute)
    }
    "status" {
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "enable" {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "disable" {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "start" {
        Start-ScheduledTask -TaskName $TaskName
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "stop" {
        Stop-ScheduledTask -TaskName $TaskName
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "uninstall" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
}
