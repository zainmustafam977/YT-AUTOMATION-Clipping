@echo off
title Shorts Engine - Create Dashboard User
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
echo   Shorts Engine - Create Dashboard User (RBAC)
echo ===================================================
echo Project Root: %CD%

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo.
set /p UNAME="Enter Username: "
if "%UNAME%"=="" (
    echo Error: Username cannot be empty.
    pause
    exit /b 1
)

echo.
echo Select User Role:
echo   1. admin    (full control including user management)
echo   2. operator (can approve clips, edit settings, manage channels)
echo   3. viewer   (read-only inspection access)
set /p RCHOICE="Enter 1, 2, or 3 [default: 2]: "

set "UROLE=operator"
if "%RCHOICE%"=="1" set "UROLE=admin"
if "%RCHOICE%"=="3" set "UROLE=viewer"

echo.
echo Creating user '%UNAME%' with role '%UROLE%'...
echo (You will be prompted to enter a password securely with masked input)
echo.

"%PYTHON_EXE%" run.py create-user --username "%UNAME%" --role "%UROLE%"
echo.
pause
