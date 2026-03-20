@echo off
setlocal
title Jushuitan SKU Offline Batch Runner

cd /d "%~dp0"

echo ========================================
echo Jushuitan SKU Offline Batch Runner
echo ========================================
echo.

if not exist ".env" (
  echo Missing .env file.
  echo Please create .env first based on .env.example.
  echo.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found.
  echo Please install Node.js and make sure npm is in PATH.
  echo.
  pause
  exit /b 1
)

if not exist "node_modules" (
  echo node_modules not found. Installing dependencies...
  call npm install
  if errorlevel 1 (
    echo npm install failed.
    echo.
    pause
    exit /b 1
  )
)

echo Starting task...
echo Output will be written to the results folder.
echo.

call npm run start
set EXIT_CODE=%ERRORLEVEL%

echo.
if "%EXIT_CODE%"=="0" (
  echo Task completed.
) else (
  echo Task failed. Exit code: %EXIT_CODE%
  echo Check the results and artifacts folders for details.
)
echo.
pause
exit /b %EXIT_CODE%
