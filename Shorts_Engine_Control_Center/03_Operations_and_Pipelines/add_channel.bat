@echo off
title Shorts Engine - Add Monitored Channel
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
echo   Shorts Engine - Add YouTube Channel to Watch
echo ===================================================
echo Project Root: %CD%

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo.
set /p CH_ID="Enter YouTube Channel ID (e.g. UCxxxx): "
if "%CH_ID%"=="" (
    echo Error: Channel ID cannot be empty.
    pause
    exit /b 1
)

set /p CH_NAME="Enter Channel Display Name: "
if "%CH_NAME%"=="" (
    set "CH_NAME=Channel_%CH_ID%"
)

set /p HR_REQ="Require Human Review before publishing? [Y/n]: "
if /i "%HR_REQ%"=="n" (
    set "HR_FLAG="
) else (
    set "HR_FLAG=--human-review"
)

echo.
echo Registering channel %CH_NAME% (%CH_ID%)...
echo.

"%PYTHON_EXE%" cli.py channel add --channel-id "%CH_ID%" --name "%CH_NAME%" %HR_FLAG%
echo.
pause
