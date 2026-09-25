@echo off
setlocal enabledelayedexpansion
title YouTube Shorts Automation - Quick Setup

echo ==============================================================================
echo        YouTube Shorts Automation - Automated Setup (Windows)
echo ==============================================================================
echo.

:: 1. Check Python installation
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not found in your PATH!
    echo Please install Python 3.11 from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: 2. Create and activate virtual environment if not present
if not exist ".venv" (
    echo [*] Creating virtual environment in .venv...
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [*] Activating virtual environment...
call .venv\Scripts\activate.bat

:: 3. Upgrade pip and install dependencies
echo [*] Installing dependencies from requirements.txt...
python -m pip install --upgrade pip
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Some dependencies had warnings during installation. Continuing setup...
)

:: 4. Run automated environment bootstrap
python setup_environment.py

echo.
echo Setup script finished.
pause
