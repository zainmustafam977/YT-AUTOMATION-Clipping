@echo off
setlocal enabledelayedexpansion
title YouTube Shorts Automation Studio Launcher
cd /d "%~dp0"

echo =====================================================================
echo    YouTube Shorts Automation Studio - Self-Healing Launcher
echo =====================================================================

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Startup encountered an error with exit code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
pause
