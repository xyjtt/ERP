[CmdletBinding()]
param(
    [ValidateSet("install", "preview", "run", "status", "enable", "disable", "start", "launch", "stop", "uninstall")]
    [string]$Action = "status",
    [string]$TaskName = "YYDD-1688-Listing-Daily",
    [string]$LauncherTaskName = "YYDD-1688-Listing-Daily-Launcher",
    [string]$DailyAt = "08:00",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SharedRuntimeRoot = "E:\1688\1688-script-new",
    [string]$CandidateRoot = "C:\ProgramData\YYDD\1688-listing\inbox",
    [string]$AccountKey = "muke_lixiang",
    [ValidateRange(1, 500)]
    [int]$MaxItems = 50,
    [ValidateRange(60, 21600)]
    [int]$TaskTimeoutSeconds = 7200,
    [int]$SessionId = 0
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$SharedRuntimeRoot = [IO.Path]::GetFullPath($SharedRuntimeRoot)
$CandidateRoot = [IO.Path]::GetFullPath($CandidateRoot)

if ($TaskName -ne "YYDD-1688-Listing-Daily") {
    throw "The formal listing task name must be YYDD-1688-Listing-Daily."
}
if ($LauncherTaskName -ne "YYDD-1688-Listing-Daily-Launcher") {
    throw "The formal listing launcher task name must be YYDD-1688-Listing-Daily-Launcher."
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
    $principal = $task.Principal
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
    }
}

function Get-ListingTaskStatus {
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
        Get-CimInstance Win32_Process -Filter "Name='explorer.exe' OR Name='msedge.exe' OR Name='powershell.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { [int]$_.SessionId -gt 0 } |
        ForEach-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
            if ($null -eq $owner -or [string]::IsNullOrWhiteSpace([string]$owner.User)) {
                return
            }
            $userMatches = @(
                ([string]$owner.User -eq $RequiredUserId),
                ([string]$owner.Domain + "\" + [string]$owner.User -eq $RequiredUserId)
            )
            if ($userMatches -contains $true) {
                [int]$_.SessionId
            }
        } |
        Sort-Object -Unique
    )
    if ($RequestedSessionId -gt 0) {
        if ($candidateIds -notcontains $RequestedSessionId) {
            throw "Requested session_id=$RequestedSessionId is not an active session for $RequiredUserId."
        }
        return $RequestedSessionId
    }
    if ($candidateIds.Count -ne 1) {
        $joined = ($candidateIds -join ",")
        throw "Expected exactly one active interactive session for $RequiredUserId; found $($candidateIds.Count) [$joined]."
    }
    return [int]$candidateIds[0]
}

function Invoke-ListingTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        throw "Formal listing task was not found: $TaskName"
    }
    if ([string]$task.State -eq "Running") {
        return [ordered]@{
            status = "already_running"
            task_name = $TaskName
            session_id = $null
            launch_mode = "already_running"
            task_state = [string]$task.State
        }
    }
    $logonType = [string]$task.Principal.LogonType
    $before = Get-ScheduledTaskInfo -TaskName $TaskName
    if ($logonType -eq "S4U") {
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Milliseconds 500
        $after = Get-ScheduledTaskInfo -TaskName $TaskName
        $current = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $observed = ([string]$current.State -eq "Running") -or ($after.LastRunTime -gt $before.LastRunTime)
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
        throw "Unsupported listing task logon type: $logonType"
    }
    $principalUserId = [string]$task.Principal.UserId
    $session = Resolve-InteractiveSessionId -RequiredUserId $principalUserId -RequestedSessionId $SessionId
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    $folder = $service.GetFolder("\")
    $registeredTask = $folder.GetTask($TaskName)
    $runningTask = $registeredTask.RunEx($null, 4, $session, $null)
    if ($null -eq $runningTask) {
        throw "Schedule.Service RunEx returned no running task for $TaskName."
    }
    Start-Sleep -Milliseconds 500
    $after = Get-ScheduledTaskInfo -TaskName $TaskName
    $current = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $observed = ([string]$current.State -eq "Running") -or ($after.LastRunTime -gt $before.LastRunTime)
    if (-not $observed) {
        throw "Schedule.Service RunEx did not produce an observable run for $TaskName in session $session."
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
            -LogonType S4U `
            -RunLevel Highest
        $task = New-ScheduledTask `
            -Action $scheduledAction `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description "Daily guarded 1688 listing drafts; approval and submit are excluded"
        Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
        $launcherArguments = @(
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", ('"' + $PSCommandPath + '"'),
            "-Action", "launch",
            "-TaskName", ('"' + $TaskName + '"'),
            "-LauncherTaskName", ('"' + $LauncherTaskName + '"'),
            "-ProjectRoot", ('"' + $ProjectRoot + '"'),
            "-SharedRuntimeRoot", ('"' + $SharedRuntimeRoot + '"'),
            "-CandidateRoot", ('"' + $CandidateRoot + '"'),
            "-AccountKey", $AccountKey,
            "-MaxItems", [string]$MaxItems,
            "-TaskTimeoutSeconds", [string]$TaskTimeoutSeconds
        ) -join " "
        $launcherAction = New-ScheduledTaskAction `
            -Execute "PowerShell.exe" `
            -Argument $launcherArguments `
            -WorkingDirectory $ProjectRoot
        $launcherTask = New-ScheduledTask `
            -Action $launcherAction `
            -Settings $settings `
            -Principal (New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest) `
            -Description "On-demand fallback for the guarded Administrator S4U listing task"
        Register-ScheduledTask -TaskName $LauncherTaskName -InputObject $launcherTask -Force | Out-Null
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
        if ($null -ne (Get-ScheduledTask -TaskName $LauncherTaskName -ErrorAction SilentlyContinue)) {
            Enable-ScheduledTask -TaskName $LauncherTaskName | Out-Null
        }
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "disable" {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        if ($null -ne (Get-ScheduledTask -TaskName $LauncherTaskName -ErrorAction SilentlyContinue)) {
            Disable-ScheduledTask -TaskName $LauncherTaskName | Out-Null
        }
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
    "start" {
        Invoke-ListingTask | ConvertTo-Json -Depth 10
    }
    "launch" {
        Invoke-ListingTask | ConvertTo-Json -Depth 10
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
        $launcherTask = Get-ScheduledTask -TaskName $LauncherTaskName -ErrorAction SilentlyContinue
        if ($null -ne $launcherTask) {
            Unregister-ScheduledTask -TaskName $LauncherTaskName -Confirm:$false
        }
        Get-ListingTaskStatus | ConvertTo-Json -Depth 10
    }
}
