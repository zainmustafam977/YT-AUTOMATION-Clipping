@echo off
title Shorts Engine - Process Video
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
echo   Shorts Engine - Manual Video Ingestion
echo ===================================================
echo Project Root: %CD%

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo.
set /p VIDEO_ID="Enter YouTube Video URL or ID: "
if "%VIDEO_ID%"=="" (
    echo Error: Video URL or ID cannot be empty.
    pause
    exit /b 1
)

set /p DRY_RUN="Dry Run (no YouTube publish)? [Y/n]: "
if /i "%DRY_RUN%"=="n" (
    set "DRY_FLAG="
) else (
    set "DRY_FLAG=--dry-run"
)

echo.
echo Starting pipeline for: %VIDEO_ID% %DRY_FLAG%
echo.

"%PYTHON_EXE%" cli.py process --video "%VIDEO_ID%" %DRY_FLAG%
echo.
echo ===================================================
echo Processing finished.
echo ===================================================
pause
