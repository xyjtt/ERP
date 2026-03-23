param(
    [string]$PythonExe = "python",
    [string]$DebuggerAddress = "127.0.0.1:9222",
    [ValidateSet("chrome", "edge")]
    [string]$Browser = "chrome",
    [switch]$UpdatePlatformLocalConfig,
    [string]$PlatformLocalConfigPath = "",
    [switch]$ReplacePlatformLocal,
    [switch]$RunDoctorAfterUpdate,
    [string]$DoctorSystem = "1688_direct",
    [string]$TemplateFile = "templates/furniture_template.csv"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$outputDir = Join-Path $projectRoot "logs\selector_probe\1688"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

Write-Host "[STEP] Capture 1688 category selection page"
Write-Host "[INFO] Navigate the attached Chrome window to the 1688 category selection page."
& $PythonExe rpa/selector_probe.py `
    --browser $Browser `
    --debugger-address $DebuggerAddress `
    --output (Join-Path $outputDir "category_page.json") `
    --screenshot (Join-Path $outputDir "category_page.png") `
    --html (Join-Path $outputDir "category_page.html")

Write-Host "[STEP] Capture 1688 publish detail page"
Write-Host "[INFO] Navigate the attached Chrome window to the 1688 publish detail page."
& $PythonExe rpa/selector_probe.py `
    --browser $Browser `
    --debugger-address $DebuggerAddress `
    --output (Join-Path $outputDir "publish_detail_page.json") `
    --screenshot (Join-Path $outputDir "publish_detail_page.png") `
    --html (Join-Path $outputDir "publish_detail_page.html")

if ($UpdatePlatformLocalConfig) {
    $targetPlatformLocalConfigPath = $PlatformLocalConfigPath
    if (-not $targetPlatformLocalConfigPath) {
        $targetPlatformLocalConfigPath = Join-Path $projectRoot "config\platforms\1688.local.json"
    }

    Write-Host "[STEP] Generate 1688 local selector overrides"
    Write-Host "[INFO] Target local config: $targetPlatformLocalConfigPath"

    $suggestArgs = @(
        "rpa/suggest_selectors.py",
        "--probe-json",
        (Join-Path $outputDir "publish_detail_page.json"),
        "--platform-local-output",
        $targetPlatformLocalConfigPath
    )

    if ($ReplacePlatformLocal) {
        $suggestArgs += "--replace-platform-local"
    }

    & $PythonExe @suggestArgs

    if ($RunDoctorAfterUpdate) {
        Write-Host "[STEP] Run doctor after updating local config"
        & $PythonExe rpa/main.py `
            --system $DoctorSystem `
            --platform 1688 `
            --file $TemplateFile `
            --doctor
    }
}

Write-Host "[DONE] 1688 selector capture completed."
