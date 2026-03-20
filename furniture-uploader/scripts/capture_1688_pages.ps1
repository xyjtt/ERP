param(
    [string]$PythonExe = "python",
    [string]$DebuggerAddress = "127.0.0.1:9222"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$outputDir = Join-Path $projectRoot "logs\selector_probe\1688"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

Write-Host "[STEP] Capture 1688 category selection page"
Write-Host "[INFO] Navigate the attached Chrome window to the 1688 category selection page."
& $PythonExe rpa/selector_probe.py `
    --debugger-address $DebuggerAddress `
    --output (Join-Path $outputDir "category_page.json") `
    --screenshot (Join-Path $outputDir "category_page.png") `
    --html (Join-Path $outputDir "category_page.html")

Write-Host "[STEP] Capture 1688 publish detail page"
Write-Host "[INFO] Navigate the attached Chrome window to the 1688 publish detail page."
& $PythonExe rpa/selector_probe.py `
    --debugger-address $DebuggerAddress `
    --output (Join-Path $outputDir "publish_detail_page.json") `
    --screenshot (Join-Path $outputDir "publish_detail_page.png") `
    --html (Join-Path $outputDir "publish_detail_page.html")

Write-Host "[DONE] 1688 selector capture completed."
