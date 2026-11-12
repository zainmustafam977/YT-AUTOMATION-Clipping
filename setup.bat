@echo off
:: ==============================================================================
::  YouTube Shorts Automation Engine - Setup Launcher
::  Double-click this file to run the complete setup.
:: ==============================================================================

:: Ensure we run from the directory where this batch file lives
cd /d "%~dp0"

echo.
echo ================================================================================
echo   YouTube Shorts Automation Engine - Setup Launcher
echo ================================================================================
echo.
echo   Starting PowerShell setup script...
echo   (A blue PowerShell window will appear - do NOT close it until done)
echo.

:: Detect PowerShell executable (prefer pwsh / PowerShell 7 for speed)
where pwsh >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PS_EXE=pwsh
) else (
    set PS_EXE=powershell
)

:: Pass any arguments forwarded from command line to the PS script
:: -ExecutionPolicy Bypass  : override any machine policy that would block .ps1 files
:: -NoProfile               : faster startup, avoid profile conflicts
:: -File                    : treats the script as a file (correct path handling)
%PS_EXE% -ExecutionPolicy Bypass -NoProfile -File "%~dp0setup.ps1" %*

:: Capture exit code
set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE% EQU 0 (
    echo   [SUCCESS] Setup completed successfully!
) else (
    echo   [ERROR] Setup finished with error code: %EXIT_CODE%
    echo   Check storage\logs\setup.log for details.
)

echo.
pause
exit /b %EXIT_CODE%
