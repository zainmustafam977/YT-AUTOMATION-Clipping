@echo off
title Shorts Engine - Dashboard Enhancements Test Suite
setlocal enabledelayedexpansion

:: Smart Project Directory Resolver
if exist "%~dp0run.py" (
    cd /d "%~dp0"
) else if exist "%~dp0..\run.py" (
    cd /d "%~dp0.."
) else if exist "%~dp0..\..\run.py" (
    cd /d "%~dp0..\.."
)

echo ===================================================
echo   Shorts Engine - Running Dashboard Tests (9 tests)
echo ===================================================
echo Project Root: %CD%

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Using Python: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" -m pytest tests/test_dashboard_enhancements.py -v
echo.
echo ===================================================
echo Test execution completed.
echo ===================================================
pause
