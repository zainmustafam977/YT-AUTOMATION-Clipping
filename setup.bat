@echo off
REM setup.bat — one-click Windows installer.
REM
REM Behaviour:
REM   - Detects Python (>= 3.11) and ffmpeg on PATH; if missing, opens
REM     a browser to the download page and pauses.
REM   - Calls setup.ps1 with -ExecutionPolicy Bypass (does NOT modify
REM     the registry).
REM   - Writes a start-menu shortcut that launches the dashboard.
REM
REM Double-click: runs setup. Second double-click: launches dashboard.
REM
REM Exit codes:
REM   0 = success
REM   1 = setup failed (see logs\setup-<timestamp>.log)
REM   2 = prerequisites missing (Python or ffmpeg)

setlocal EnableExtensions EnableDelayedExpansion

REM ----- 1. Detect PowerShell 7 ------------------------------------------------
where pwsh >nul 2>nul
if errorlevel 1 (
    echo PowerShell 7 (pwsh) is required.
    echo Install from https://github.com/PowerShell/PowerShell/releases
    pause
    exit /b 2
)

REM ----- 2. If already set up, launch instead of installing -------------------
if exist "%~dp0.setup-state.json" (
    if "%~1"=="-launch" goto :launch
    if exist "%~dp0.venv\Scripts\python.exe" goto :launch
)

REM ----- 3. Detect Python and ffmpeg -----------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ is required.
    start "" https://www.python.org/downloads/
    pause
    exit /b 2
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo ffmpeg is required.
    start "" https://ffmpeg.org/download.html
    pause
    exit /b 2
)

REM ----- 4. Run setup.ps1 ----------------------------------------------------
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Mode install
if errorlevel 1 exit /b 1
exit /b 0

:launch
start "" pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Mode launch
exit /b 0
