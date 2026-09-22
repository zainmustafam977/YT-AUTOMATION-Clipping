@echo off
title Shorts Engine - Background Worker Queue
setlocal enabledelayedexpansion

:: Smart Project Directory Resolver
if exist "%~dp0run.py" (
    cd /d "%~dp0"
) else if exist "%~dp0..\run.py" (
    cd /d "%~dp0.."
) else if exist "%~dp0..\..\run.py" (
    cd /d "%~dp0..\.."
) else if exist "D:\Projects\YT AUTOMATION\run.py" (
    cd /d "D:\Projects\YT AUTOMATION"
) else if exist "%USERPROFILE%\Desktop\shorts_m3_audit\run.py" (
    cd /d "%USERPROFILE%\Desktop\shorts_m3_audit"
)

echo ===================================================
echo   Shorts Engine - Starting Background Worker
echo ===================================================
echo Project Root: %CD%

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Using Python: %PYTHON_EXE%
echo Worker is polling queue for pending clips...
echo Press Ctrl+C to stop.
echo.

"%PYTHON_EXE%" run.py worker
pause
