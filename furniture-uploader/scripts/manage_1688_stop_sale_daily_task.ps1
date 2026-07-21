[CmdletBinding()]
param(
    [ValidateSet("install", "preview", "run", "status", "enable", "disable", "start", "stop", "uninstall")]
    [string]$Action = "status",
    [string]$TaskName = "YYDD-1688-Stop-Sale-Daily",
    [string]$DailyAt = "13:00",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SharedRuntimeRoot = "E:\1688\1688-script-new",
    [string]$JushuitanRoot = "",
    [string]$WorkerTaskName = "YYDD-1688-Crawler-Worker",
    [string]$BusinessDate = "",
    [ValidateRange(1, 1000)]
    [int]$BatchSize = 25
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if ([string]::IsNullOrWhiteSpace($JushuitanRoot)) {
    $JushuitanRoot = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $ProjectRoot) "jushuitan-sku-offline-batch"))
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

function Get-StopSaleTaskStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $latestSummaryPath = Join-Path $ProjectRoot "logs\sku_offline\scheduler\latest.summary.json"
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
    $triggers = @($task.Triggers | ForEach-Object {
        [ordered]@{
            start_boundary = [string]$_.StartBoundary
            enabled = [bool]$_.Enabled
            days_interval = $_.DaysInterval
        }
    })
    return [ordered]@{
        task_name = $TaskName
        exists = $true
        state = [string]$task.State
        enabled = ([string]$task.State -ne "Disabled")
        last_run_time = $info.LastRunTime
        last_task_result = $info.LastTaskResult
        next_run_time = $info.NextRunTime
        missed_runs = $info.NumberOfMissedRuns
        triggers = $triggers
        latest_summary_path = $latestSummaryPath
        latest_summary = $latestSummary
    }
}

function Invoke-DailyRun {
    param(
        [ValidateSet("preview", "execute")]
        [string]$Mode = "execute"
    )

    $python = Resolve-PythonExecutable
    $runner = Join-Path $PSScriptRoot "manage_1688_stop_sale_daily.py"
    if (-not (Test-Path -LiteralPath $runner)) {
        throw "Daily manager script was not found: $runner"
    }
    $logRoot = Join-Path $ProjectRoot "logs\sku_offline\scheduler\scheduled_logs"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logPath = Join-Path $logRoot "$timestamp.log"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    Push-Location $ProjectRoot
    try {
        $arguments = @(
            $runner, "run",
            "--mode", $Mode,
            "--shared-runtime-root", $SharedRuntimeRoot,
            "--jushuitan-root", $JushuitanRoot,
            "--worker-task-name", $WorkerTaskName,
            "--batch-size", [string]$BatchSize
        )
        if ($Mode -eq "execute") {
            $arguments += "--yes"
        }
        if (-not [string]::IsNullOrWhiteSpace($BusinessDate)) {
            $arguments += @("--date", $BusinessDate)
        }
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
        $managerScript = $PSCommandPath
        $taskArguments = @(
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", ('"' + $managerScript + '"'),
            "-Action", "run",
            "-TaskName", ('"' + $TaskName + '"'),
            "-ProjectRoot", ('"' + $ProjectRoot + '"'),
            "-SharedRuntimeRoot", ('"' + $SharedRuntimeRoot + '"'),
            "-JushuitanRoot", ('"' + $JushuitanRoot + '"'),
            "-WorkerTaskName", ('"' + $WorkerTaskName + '"'),
            "-BatchSize", [string]$BatchSize
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
            -Description "Daily full 1688 SKU stop-sale and Jushuitan cleanup"
        Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
        Get-StopSaleTaskStatus | ConvertTo-Json -Depth 10
    }
    "preview" {
        exit (Invoke-DailyRun -Mode preview)
    }
    "run" {
        exit (Invoke-DailyRun -Mode execute)
    }
    "status" {
        Get-StopSaleTaskStatus | ConvertTo-Json -Depth 10
    }
    "enable" {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        Get-StopSaleTaskStatus | ConvertTo-Json -Depth 10
    }
    "disable" {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        Get-StopSaleTaskStatus | ConvertTo-Json -Depth 10
    }
    "start" {
        Start-ScheduledTask -TaskName $TaskName
        Get-StopSaleTaskStatus | ConvertTo-Json -Depth 10
    }
    "stop" {
        Stop-ScheduledTask -TaskName $TaskName
        Get-StopSaleTaskStatus | ConvertTo-Json -Depth 10
    }
    "uninstall" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Get-StopSaleTaskStatus | ConvertTo-Json -Depth 10
    }
}
