param(
    [int]$Port = 9222,
    [string]$Url = "https://work.1688.com/home/page/index.htm",
    [string]$ProfileDir = ".chrome-debug-profile",
    [string]$ChromeExe = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Resolve-ChromePath {
    param([string]$PreferredPath)

    if ($PreferredPath -and (Test-Path $PreferredPath)) {
        return $PreferredPath
    }

    $candidates = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "Chrome executable not found. Pass -ChromeExe explicitly."
}

$resolvedChrome = Resolve-ChromePath -PreferredPath $ChromeExe
$resolvedProfileDir = Join-Path $projectRoot $ProfileDir
New-Item -ItemType Directory -Force -Path $resolvedProfileDir | Out-Null

Write-Host "[INFO] Project root: $projectRoot"
Write-Host "[INFO] Chrome: $resolvedChrome"
Write-Host "[INFO] Remote debugging port: $Port"
Write-Host "[INFO] Debug profile: $resolvedProfileDir"
Write-Host "[INFO] Opening: $Url"

$arguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$resolvedProfileDir",
    "--new-window",
    $Url
)

Start-Process -FilePath $resolvedChrome -ArgumentList $arguments | Out-Null

Write-Host "[DONE] Debug Chrome launched."
Write-Host "[NEXT] Complete login in the opened browser, then continue with selector capture."
