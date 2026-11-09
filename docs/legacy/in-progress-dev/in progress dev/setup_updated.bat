@echo off
:: ==============================================================================
::  YouTube Shorts Automation Engine - Setup Launcher (v3.2)
::  Double-click to run the complete, self-healing setup.
::  Extra arguments are forwarded, e.g.:  setup.bat -GPU   |  setup.bat -Force
::  Set YT_SETUP_NOPAUSE=1 to skip the final "press any key" (CI / scripting).
:: ==============================================================================
setlocal EnableExtensions

pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Cannot open the folder "%~dp0"
    echo         Network/UNC paths are not supported - copy the project to a local drive.
    if not defined YT_SETUP_NOPAUSE pause
    exit /b 1
)

title Shorts Engine - Setup

echo.
echo ================================================================================
echo   YouTube Shorts Automation Engine - Setup Launcher
echo ================================================================================
echo.

if not exist "%~dp0setup.ps1" (
    echo [ERROR] setup.ps1 was not found next to setup.bat:
    echo         "%~dp0setup.ps1"
    echo         Make sure both files are in the project root folder.
    set "RC=1"
    goto :finish
)

:: Prefer Windows PowerShell 5.1 (always present, the tested target); fall back to PowerShell 7.
set "PS_EXE="
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not defined PS_EXE (
    where pwsh >nul 2>&1
    if not errorlevel 1 set "PS_EXE=pwsh"
)
if not defined PS_EXE (
    where powershell >nul 2>&1
    if not errorlevel 1 set "PS_EXE=powershell"
)
if not defined PS_EXE (
    echo [ERROR] PowerShell was not found on this computer.
    echo         Install "Windows PowerShell 5.1" or PowerShell 7 and run this file again.
    set "RC=1"
    goto :finish
)

echo   Starting setup (a live progress display will follow).
echo   Do NOT close this window until it says that setup has finished.
echo.

:: -ExecutionPolicy Bypass : override any machine policy that blocks .ps1 files
:: -NoProfile              : faster start, avoids profile conflicts
:: YT_SETUP_FROM_BAT=1     : tells setup.ps1 that this launcher will handle the final pause
set "YT_SETUP_FROM_BAT=1"
"%PS_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "RC=%ERRORLEVEL%"

:finish
echo.
echo ================================================================================
if "%RC%"=="0" (
    echo   [SUCCESS] Setup completed successfully.
    echo   Start the app with:  tools\launchers\run_dashboard.bat
) else if "%RC%"=="2" (
    echo   [ATTENTION] Setup finished WITH ERRORS - review the summary above.
    echo   Fix the listed problems and run setup.bat again ^(it is safe to re-run^).
    echo   Details: storage\logs\setup.log
) else (
    echo   [FAILED] Setup could not complete ^(exit code %RC%^).
    echo   Details: storage\logs\setup.log
)
echo ================================================================================
echo.
if not defined YT_SETUP_NOPAUSE pause
popd >nul 2>&1
exit /b %RC%
