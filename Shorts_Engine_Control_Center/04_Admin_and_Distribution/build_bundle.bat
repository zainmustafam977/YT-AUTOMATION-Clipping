@echo off
title Shorts Engine - Package Distribution Bundle
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
echo   Shorts Engine - Rebuilding Distribution Bundle
echo ===================================================
echo Project Root: %CD%

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Packaging repository into shorts_automation_m3_bundle.zip...
"%PYTHON_EXE%" -c "import zipfile; from pathlib import Path; root = Path('.'); out_zip = Path('shorts_automation_m3_bundle.zip'); excludes = {'.git', '.pytest_cache', '__pycache__', 'venv', '.venv', 'downloads', 'shorts_automation_m3_bundle.zip', 'shorts_automation_m2_bundle.zip'}; zf = zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED); [zf.write(p, p.relative_to(root)) for p in root.rglob('*') if not any(part in excludes for part in p.parts) and p.is_file()]; zf.close(); print(f'Archive created: {out_zip.stat().st_size:,} bytes')"

echo.
echo Computing SHA-256 Hash...
certutil -hashfile shorts_automation_m3_bundle.zip SHA256
echo.
pause
