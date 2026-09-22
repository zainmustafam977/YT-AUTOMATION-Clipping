@echo off
title Shorts Engine - Control Center
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

:MENU
cls
echo ================================================================
echo           *** SHORTS ENGINE - MASTER CONTROL CENTER ***
echo ================================================================
echo   Project Root: %CD%
echo.
echo   --- [1] SERVERS AND WORKERS ---
echo     [1] Start Web Dashboard        (http://127.0.0.1:8000/)
echo     [2] Start Background Worker    (process rendering/queue)
echo.
echo   --- [2] TESTING AND VALIDATION ---
echo     [3] Run All Automated Tests    (full 89-test suite)
echo     [4] Run Dashboard Tests Only   (9 enhancement tests)
echo.
echo   --- [3] OPERATIONS AND PIPELINES ---
echo     [5] Ingest / Process Video     (URL or Video ID)
echo     [6] Add Monitored Channel      (YouTube channel ID)
echo.
echo   --- [4] ADMIN AND DISTRIBUTION ---
echo     [7] Create / Reset User        (Admin, Operator, Viewer)
echo     [8] Rebuild Distribution Zip   (package bundle + SHA256)
echo.
echo     [9] Exit
echo ================================================================
set /p MCHOICE="Select an option [1-9]: "

if "%MCHOICE%"=="1" goto OP_DASH
if "%MCHOICE%"=="2" goto OP_WORKER
if "%MCHOICE%"=="3" goto OP_TESTS
if "%MCHOICE%"=="4" goto OP_DASH_TESTS
if "%MCHOICE%"=="5" goto OP_PROCESS
if "%MCHOICE%"=="6" goto OP_CHANNEL
if "%MCHOICE%"=="7" goto OP_USER
if "%MCHOICE%"=="8" goto OP_BUNDLE
if "%MCHOICE%"=="9" exit /b 0

echo Invalid choice. Please select 1 through 9.
timeout /t 2 >nul
goto MENU

:OP_DASH
call "%~dp001_Servers_and_Workers\start_dashboard.bat"
goto MENU

:OP_WORKER
call "%~dp001_Servers_and_Workers\start_worker.bat"
goto MENU

:OP_TESTS
call "%~dp002_Testing_and_Validation\run_all_tests.bat"
goto MENU

:OP_DASH_TESTS
call "%~dp002_Testing_and_Validation\run_dashboard_tests.bat"
goto MENU

:OP_PROCESS
call "%~dp003_Operations_and_Pipelines\process_video.bat"
goto MENU

:OP_CHANNEL
call "%~dp003_Operations_and_Pipelines\add_channel.bat"
goto MENU

:OP_USER
call "%~dp004_Admin_and_Distribution\create_user.bat"
goto MENU

:OP_BUNDLE
call "%~dp004_Admin_and_Distribution\build_bundle.bat"
goto MENU
