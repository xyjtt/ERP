param(
    [int]$Port = 9222,
    [string]$Url = "https://offer-new.1688.com/select.htm",
    [string]$ProfileDir = ".edge-debug-profile",
    [string]$EdgeExe = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Resolve-EdgePath {
    param([string]$PreferredPath)

    if ($PreferredPath -and (Test-Path $PreferredPath)) {
        return $PreferredPath
    }

    $candidates = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "Edge executable not found. Pass -EdgeExe explicitly."
}

$resolvedEdge = Resolve-EdgePath -PreferredPath $EdgeExe
$resolvedProfileDir = if ([System.IO.Path]::IsPathRooted($ProfileDir)) {
    $ProfileDir
}
else {
    Join-Path $projectRoot $ProfileDir
}
New-Item -ItemType Directory -Force -Path $resolvedProfileDir | Out-Null

Write-Host "[INFO] Project root: $projectRoot"
Write-Host "[INFO] Edge: $resolvedEdge"
Write-Host "[INFO] Remote debugging port: $Port"
Write-Host "[INFO] Debug profile: $resolvedProfileDir"
Write-Host "[INFO] Opening: $Url"

$arguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$resolvedProfileDir",
    "--new-window",
    $Url
)

Start-Process -FilePath $resolvedEdge -ArgumentList $arguments | Out-Null

Write-Host "[DONE] Debug Edge launched."
Write-Host "[NEXT] Complete login in the opened browser, then continue with selector capture."
