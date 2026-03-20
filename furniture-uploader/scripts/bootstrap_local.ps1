param(
    [string]$PythonExe = "python",
    [string]$DbConfigPath = "config/database.local.json",
    [switch]$SkipInstall,
    [switch]$SkipDbCheck
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "[STEP] Project root: $projectRoot"

if (-not $SkipInstall) {
    Write-Host "[STEP] Installing Python requirements..."
    & $PythonExe -m pip install -r requirements.txt
}

Write-Host "[STEP] Initializing database local config template..."
& $PythonExe rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --init-db-config --db-config $DbConfigPath

Write-Host "[STEP] Initializing operator local config template..."
& $PythonExe rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --init-operator-local-config

Write-Host "[STEP] Initializing selector local config templates..."
& $PythonExe rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --init-local-config

Write-Host "[STEP] Running environment checks..."
& $PythonExe rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-env

Write-Host "[STEP] Running config doctor..."
& $PythonExe rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --doctor

Write-Host "[STEP] Running unit tests..."
& $PythonExe -m unittest discover -s tests -p "test_*.py"

if (-not $SkipDbCheck -and (Test-Path $DbConfigPath)) {
    Write-Host "[STEP] Running database checks..."
    & $PythonExe rpa/main.py --system jushuitan --platform 1688 --file templates/furniture_template.csv --check-db --db-config $DbConfigPath
}
else {
    Write-Host "[INFO] Skip database check. Ensure $DbConfigPath exists and env password is configured."
}

Write-Host "[DONE] Bootstrap completed."
