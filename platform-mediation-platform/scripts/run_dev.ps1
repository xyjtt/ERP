$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$srcPath = Join-Path $projectRoot "src"

$env:PYTHONPATH = $srcPath
python -m uvicorn platform_mediation.main:app --host 127.0.0.1 --port 8000 --reload

