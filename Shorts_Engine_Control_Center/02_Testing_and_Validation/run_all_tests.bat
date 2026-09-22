@echo off
title Shorts Engine - Run All Automated Tests
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
echo   Shorts Engine - Running Full Test Suite (89 tests)
echo ===================================================
echo Project Root: %CD%

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Using Python: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" -m pytest -v
echo.
echo ===================================================
echo Test execution completed.
echo ===================================================
pause
