$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --name jushuitan-link-ops `
  --add-data "config;config" `
  --add-data "docs;docs" `
  --add-data "sql;sql" `
  --add-data "templates;templates" `
  main.py

New-Item -ItemType Directory -Force "dist\jushuitan-link-ops\logs" | Out-Null
Write-Host "Build completed: dist\jushuitan-link-ops\jushuitan-link-ops.exe"
