param(
    [string]$RemoteName = "gitee",
    [string]$SourceBranch = "codex/1688-auto-listing-20260724",
    [string]$DeployBranch = "deploy/gitee-auto-listing-20260724",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path $repo "furniture-uploader\artifacts\gitee-update-latest.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-UpdateResult {
    param([hashtable]$Result)
    $Result["checked_at"] = [DateTime]::UtcNow.ToString("o")
    $json = $Result | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($OutputPath, $json + [Environment]::NewLine, $utf8)
    Write-Output $json
}

try {
    $branch = (git -C $repo branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne $DeployBranch) {
        throw "Expected deploy branch '$DeployBranch', found '$branch'."
    }

    $trackedChanges = @(git -C $repo status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0 -or $trackedChanges.Count -gt 0) {
        throw "Tracked worktree changes block Gitee update."
    }

    $runningTasks = @(
        Get-ScheduledTask -ErrorAction SilentlyContinue |
            Where-Object {
                $_.State -eq "Running" -and
                $_.TaskName -like "YYDD-1688-Listing-*" -and
                $_.TaskName -ne "YYDD-1688-Listing-Gitee-Update"
            } |
            Select-Object -ExpandProperty TaskName
    )
    if ($runningTasks.Count -gt 0) {
        throw "Listing tasks are running: $($runningTasks -join ', ')."
    }

    $runningProcesses = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -in @("python.exe", "pythonw.exe", "msedgedriver.exe") -and
                $_.CommandLine -like "*$repo*"
            } |
            ForEach-Object { "$($_.Name):$($_.ProcessId)" }
    )
    if ($runningProcesses.Count -gt 0) {
        throw "Listing repository processes are running: $($runningProcesses -join ', ')."
    }

    $ErrorActionPreference = "Continue"
    $fetchOutput = @(git -C $repo fetch $RemoteName $SourceBranch 2>&1)
    $fetchCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($fetchCode -ne 0) {
        throw "Gitee fetch failed with exit code $fetchCode."
    }

    $remoteRef = "$RemoteName/$SourceBranch"
    $before = (git -C $repo rev-parse HEAD).Trim()
    $target = (git -C $repo rev-parse $remoteRef).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $target) {
        throw "Fetched branch '$remoteRef' cannot be resolved."
    }

    $ErrorActionPreference = "Continue"
    $mergeOutput = @(git -C $repo merge --ff-only $remoteRef 2>&1)
    $mergeCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($mergeCode -ne 0) {
        throw "Fast-forward update failed with exit code $mergeCode."
    }

    $after = (git -C $repo rev-parse HEAD).Trim()
    if ($after -ne $target) {
        throw "Updated HEAD '$after' does not match fetched target '$target'."
    }
    Write-UpdateResult @{
        status = "passed"
        repository = $repo
        deploy_branch = $DeployBranch
        source_ref = $remoteRef
        before_head = $before
        after_head = $after
        changed = ($before -ne $after)
        fetch_output = @($fetchOutput | ForEach-Object { [string]$_ })
        merge_output = @($mergeOutput | ForEach-Object { [string]$_ })
    }
    exit 0
} catch {
    Write-UpdateResult @{
        status = "blocked"
        repository = $repo
        deploy_branch = $DeployBranch
        source_ref = "$RemoteName/$SourceBranch"
        error_type = $_.Exception.GetType().FullName
        error = $_.Exception.Message
    }
    exit 1
}
