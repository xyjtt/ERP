@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python not found: py or python command missing.
    echo Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

%PYTHON_CMD% -X utf8 scripts\launcher_gui.py
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Launcher failed with exit code: %EXIT_CODE%
    pause
)

exit /b %EXIT_CODE%
