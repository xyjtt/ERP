[CmdletBinding()]
param(
    [ValidateSet("install", "preview", "run", "status", "enable", "disable", "start", "launch", "stop", "uninstall")]
    [string]$Action = "status",
    [string]$TaskName = "YYDD-1688-Replace-Daily",
    [string]$LauncherTaskName = "YYDD-1688-Replace-Daily-Launcher",
    [string]$DailyAt = "14:00",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SharedRuntimeRoot = "E:\1688\1688-script-new",
    [string]$JushuitanRoot = "",
    [string]$WorkerTaskName = "YYDD-1688-Crawler-Worker",
    [string]$BusinessDate = "",
    [string]$SourceDatabase = "JSReportReplica",
    [string]$SourceTable = "app.op_stop_sale",
    [string]$SourceDriver = "ODBC Driver 17 for SQL Server",
    [ValidateRange(1, 1000)]
    [int]$BatchSize = 10,
    [ValidateRange(1, 5)]
    [int]$BatchMaxAttempts = 2,
    [ValidateRange(0, 21600)]
    [int]$LockWaitSeconds = 0,
    [ValidateRange(0, 21600)]
    [int]$JushuitanLockWaitSeconds = 3600,
    [ValidateRange(60, 21600)]
    [int]$CrawlerTaskWaitSeconds = 4800,
    [ValidateRange(60, 21600)]
    [int]$Replace1688TimeoutSeconds = 3600,
    [ValidateRange(60, 21600)]
    [int]$ReplaceJushuitanTimeoutSeconds = 1800,
    [int]$SessionId = 0
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$SharedRuntimeRoot = [IO.Path]::GetFullPath($SharedRuntimeRoot)
if ([string]::IsNullOrWhiteSpace($JushuitanRoot)) {
    $JushuitanRoot = [IO.Path]::GetFullPath(
        (Join-Path (Split-Path -Parent $ProjectRoot) "jushuitan-sku-offline-batch")
    )
}
else {
    $JushuitanRoot = [IO.Path]::GetFullPath($JushuitanRoot)
}

if ($TaskName -ne "YYDD-1688-Replace-Daily") {
    throw "The formal SKU-replace task name must be YYDD-1688-Replace-Daily."
}
if ($LauncherTaskName -ne "YYDD-1688-Replace-Daily-Launcher") {
    throw "The formal SKU-replace launcher task name must be YYDD-1688-Replace-Daily-Launcher."
}

function Resolve-PythonExecutable {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "python.exe was not found. Install dependencies or create .venv first."
    }
    return $command.Source
}

function Get-ScheduledTaskSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        return [ordered]@{
            task_name = $Name
            exists = $false
            state = "Missing"
            enabled = $false
        }
    }
    $info = Get-ScheduledTaskInfo -TaskName $Name
    return [ordered]@{
        task_name = $Name
        exists = $true
        state = [string]$task.State
        enabled = ([string]$task.State -ne "Disabled")
        last_run_time = $info.LastRunTime
        last_task_result = $info.LastTaskResult
        next_run_time = $info.NextRunTime
        missed_runs = $info.NumberOfMissedRuns
        principal = [ordered]@{
            user_id = [string]$task.Principal.UserId
            logon_type = [string]$task.Principal.LogonType
            run_level = [string]$task.Principal.RunLevel
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
    }
}

function Get-ReplaceTaskStatus {
    $latestSummaryPath = Join-Path $ProjectRoot "logs\sku_replace\scheduler\latest.summary.json"
    $latestSummary = $null
    if (Test-Path -LiteralPath $latestSummaryPath -PathType Leaf) {
        try {
            $latestSummary = Get-Content -LiteralPath $latestSummaryPath -Raw -Encoding UTF8 |
                ConvertFrom-Json
        }
        catch {
            $latestSummary = [ordered]@{ status = "unreadable" }
        }
    }
    $result = Get-ScheduledTaskSnapshot -Name $TaskName
    $result["latest_summary_path"] = $latestSummaryPath
    $result["latest_summary"] = $latestSummary
    $result["launcher"] = Get-ScheduledTaskSnapshot -Name $LauncherTaskName
    return $result
}

function Resolve-InteractiveSessionId {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RequiredUserId,
        [int]$RequestedSessionId = 0
    )

    $candidateIds = @(
        Get-CimInstance Win32_Process `
            -Filter "Name='explorer.exe' OR Name='msedge.exe' OR Name='powershell.exe' OR Name='python.exe'" `
            -ErrorAction SilentlyContinue |
        Where-Object { [int]$_.SessionId -gt 0 } |
        ForEach-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
            if ($null -eq $owner -or [string]::IsNullOrWhiteSpace([string]$owner.User)) {
                return
            }
            $matches = @(
                ([string]$owner.User -eq $RequiredUserId),
                ([string]$owner.Domain + "\" + [string]$owner.User -eq $RequiredUserId)
            )
            if ($matches -contains $true) {
                [int]$_.SessionId
            }
        } |
        Sort-Object -Unique
    )
    if ($RequestedSessionId -gt 0) {
        if ($candidateIds -notcontains $RequestedSessionId) {
            throw "Requested session_id=$RequestedSessionId is not active for $RequiredUserId."
        }
        return $RequestedSessionId
    }
    if ($candidateIds.Count -ne 1) {
        throw "Expected exactly one active session for $RequiredUserId; found $($candidateIds.Count) [$($candidateIds -join ',')]."
    }
    return [int]$candidateIds[0]
}

function Invoke-ReplaceTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        throw "Formal SKU-replace task was not found: $TaskName"
    }
    if ([string]$task.State -eq "Running") {
        return [ordered]@{
            status = "already_running"
            task_name = $TaskName
            launch_mode = "already_running"
            task_state = [string]$task.State
        }
    }

    $before = Get-ScheduledTaskInfo -TaskName $TaskName
    $logonType = [string]$task.Principal.LogonType
    if ($logonType -eq "S4U") {
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Milliseconds 500
        $after = Get-ScheduledTaskInfo -TaskName $TaskName
        $current = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $observed = ([string]$current.State -eq "Running") -or
            ($after.LastRunTime -gt $before.LastRunTime)
        if (-not $observed) {
            throw "Start-ScheduledTask did not produce an observable S4U run for $TaskName."
        }
        return [ordered]@{
            status = "started"
            task_name = $TaskName
            session_id = 0
            launch_mode = "s4u"
            task_state = [string]$current.State
            last_run_time = $after.LastRunTime
            last_task_result = $after.LastTaskResult
        }
    }
    if ($logonType -ne "Interactive") {
        throw "Unsupported SKU-replace task logon type: $logonType"
    }

    $session = Resolve-InteractiveSessionId `
        -RequiredUserId ([string]$task.Principal.UserId) `
        -RequestedSessionId $SessionId
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    $registeredTask = $service.GetFolder("\").GetTask($TaskName)
    $runningTask = $registeredTask.RunEx($null, 4, $session, $null)
    if ($null -eq $runningTask) {
        throw "Schedule.Service RunEx returned no running task for $TaskName."
    }
    Start-Sleep -Milliseconds 500
    $after = Get-ScheduledTaskInfo -TaskName $TaskName
    $current = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $observed = ([string]$current.State -eq "Running") -or
        ($after.LastRunTime -gt $before.LastRunTime)
    if (-not $observed) {
        throw "Schedule.Service RunEx did not produce a run for $TaskName in session $session."
    }
    return [ordered]@{
        status = "started"
        task_name = $TaskName
        session_id = $session
        launch_mode = "interactive_runex"
        run_ex_flags = 4
        task_state = [string]$current.State
        last_run_time = $after.LastRunTime
        last_task_result = $after.LastTaskResult
        running_task_name = [string]$runningTask.TaskName
    }
}

function Invoke-ReplaceDaily {
    param(
        [ValidateSet("preview", "execute")]
        [string]$Mode
    )

    $runner = Join-Path $PSScriptRoot "manage_1688_sku_replace_daily.py"
    if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
        throw "Daily SKU-replace manager was not found: $runner"
    }
    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        throw "SKU-replace project root does not exist: $ProjectRoot"
    }
    if (-not (Test-Path -LiteralPath $SharedRuntimeRoot -PathType Container)) {
        throw "Shared 1688 runtime root does not exist: $SharedRuntimeRoot"
    }
    if (-not (Test-Path -LiteralPath $JushuitanRoot -PathType Container)) {
        throw "Jushuitan project root does not exist: $JushuitanRoot"
    }

    $python = Resolve-PythonExecutable
    $logRoot = Join-Path $ProjectRoot "logs\sku_replace\scheduler\scheduled_logs"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $logPath = Join-Path $logRoot ((Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $arguments = @(
        $runner, "run",
        "--mode", $Mode,
        "--shared-runtime-root", $SharedRuntimeRoot,
        "--jushuitan-root", $JushuitanRoot,
        "--worker-task-name", $WorkerTaskName,
        "--source-database", $SourceDatabase,
        "--source-table", $SourceTable,
        "--source-driver", $SourceDriver,
        "--batch-size", [string]$BatchSize,
        "--batch-max-attempts", [string]$BatchMaxAttempts,
        "--lock-wait-seconds", [string]$LockWaitSeconds,
        "--jushuitan-lock-wait-seconds", [string]$JushuitanLockWaitSeconds,
        "--crawler-task-wait-seconds", [string]$CrawlerTaskWaitSeconds,
        "--1688-timeout-seconds", [string]$Replace1688TimeoutSeconds,
        "--jushuitan-timeout-seconds", [string]$ReplaceJushuitanTimeoutSeconds
    )
    if ($Mode -eq "execute") {
        $arguments += "--yes"
    }
    if (-not [string]::IsNullOrWhiteSpace($BusinessDate)) {
        $arguments += @("--date", $BusinessDate)
    }

    Push-Location $ProjectRoot
    try {
        & $python @arguments *>> $logPath
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

function New-ReplaceTaskArguments {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("run", "launch")]
        [string]$RequestedAction
    )
    return @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"'),
        "-Action", $RequestedAction,
        "-TaskName", ('"' + $TaskName + '"'),
        "-LauncherTaskName", ('"' + $LauncherTaskName + '"'),
        "-ProjectRoot", ('"' + $ProjectRoot + '"'),
        "-SharedRuntimeRoot", ('"' + $SharedRuntimeRoot + '"'),
        "-JushuitanRoot", ('"' + $JushuitanRoot + '"'),
        "-WorkerTaskName", ('"' + $WorkerTaskName + '"'),
        "-SourceDatabase", ('"' + $SourceDatabase + '"'),
        "-SourceTable", ('"' + $SourceTable + '"'),
        "-SourceDriver", ('"' + $SourceDriver + '"'),
        "-BatchSize", [string]$BatchSize,
        "-BatchMaxAttempts", [string]$BatchMaxAttempts,
        "-LockWaitSeconds", [string]$LockWaitSeconds,
        "-JushuitanLockWaitSeconds", [string]$JushuitanLockWaitSeconds,
        "-CrawlerTaskWaitSeconds", [string]$CrawlerTaskWaitSeconds,
        "-Replace1688TimeoutSeconds", [string]$Replace1688TimeoutSeconds,
        "-ReplaceJushuitanTimeoutSeconds", [string]$ReplaceJushuitanTimeoutSeconds
    ) -join " "
}

switch ($Action) {
    "install" {
        [void][datetime]::ParseExact(
            $DailyAt,
            "HH:mm",
            [Globalization.CultureInfo]::InvariantCulture
        )
        if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
            throw "SKU-replace project root does not exist: $ProjectRoot"
        }
        if (-not (Test-Path -LiteralPath $SharedRuntimeRoot -PathType Container)) {
            throw "Shared 1688 runtime root does not exist: $SharedRuntimeRoot"
        }
        if (-not (Test-Path -LiteralPath $JushuitanRoot -PathType Container)) {
            throw "Jushuitan project root does not exist: $JushuitanRoot"
        }

        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Hours 24) `
            -MultipleInstances IgnoreNew `
            -RestartCount 1 `
            -RestartInterval (New-TimeSpan -Minutes 15)
        $scheduledAction = New-ScheduledTaskAction `
            -Execute "PowerShell.exe" `
            -Argument (New-ReplaceTaskArguments -RequestedAction "run") `
            -WorkingDirectory $ProjectRoot
        $task = New-ScheduledTask `
            -Action $scheduledAction `
            -Trigger (New-ScheduledTaskTrigger -Daily -At $DailyAt) `
            -Settings $settings `
            -Principal (New-ScheduledTaskPrincipal `
                -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
                -LogonType S4U `
                -RunLevel Highest) `
            -Description "Daily guarded 1688 SKU replacement and Jushuitan synchronization"
        Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

        $launcherAction = New-ScheduledTaskAction `
            -Execute "PowerShell.exe" `
            -Argument (New-ReplaceTaskArguments -RequestedAction "launch") `
            -WorkingDirectory $ProjectRoot
        $launcherTask = New-ScheduledTask `
            -Action $launcherAction `
            -Settings $settings `
            -Principal (New-ScheduledTaskPrincipal `
                -UserId "SYSTEM" `
                -LogonType ServiceAccount `
                -RunLevel Highest) `
            -Description "On-demand fallback for the guarded Administrator S4U SKU-replace task"
        Register-ScheduledTask `
            -TaskName $LauncherTaskName `
            -InputObject $launcherTask `
            -Force | Out-Null
        Get-ReplaceTaskStatus | ConvertTo-Json -Depth 10
    }
    "preview" {
        exit (Invoke-ReplaceDaily -Mode preview)
    }
    "run" {
        exit (Invoke-ReplaceDaily -Mode execute)
    }
    "status" {
        Get-ReplaceTaskStatus | ConvertTo-Json -Depth 10
    }
    "enable" {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        if ($null -ne (Get-ScheduledTask -TaskName $LauncherTaskName -ErrorAction SilentlyContinue)) {
            Enable-ScheduledTask -TaskName $LauncherTaskName | Out-Null
        }
        Get-ReplaceTaskStatus | ConvertTo-Json -Depth 10
    }
    "disable" {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        if ($null -ne (Get-ScheduledTask -TaskName $LauncherTaskName -ErrorAction SilentlyContinue)) {
            Disable-ScheduledTask -TaskName $LauncherTaskName | Out-Null
        }
        Get-ReplaceTaskStatus | ConvertTo-Json -Depth 10
    }
    "start" {
        Invoke-ReplaceTask | ConvertTo-Json -Depth 10
    }
    "launch" {
        Invoke-ReplaceTask | ConvertTo-Json -Depth 10
    }
    "stop" {
        Stop-ScheduledTask -TaskName $TaskName
        Get-ReplaceTaskStatus | ConvertTo-Json -Depth 10
    }
    "uninstall" {
        foreach ($name in @($TaskName, $LauncherTaskName)) {
            if ($null -ne (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
            }
        }
        Get-ReplaceTaskStatus | ConvertTo-Json -Depth 10
    }
}
