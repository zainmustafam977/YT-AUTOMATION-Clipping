# ==============================================================================
# YouTube Shorts Automation - Automated Setup (PowerShell)
# ==============================================================================

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "        YouTube Shorts Automation - Automated Setup (PowerShell)" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python installation
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "[ERROR] Python was not found in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.11 from https://www.python.org/downloads/ and check 'Add Python to PATH'."
    exit 1
}

# 2. Create virtual environment if missing
if (-not (Test-Path ".venv")) {
    Write-Host "[*] Creating virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
}

# 3. Activate virtual environment
Write-Host "[*] Activating virtual environment..." -ForegroundColor Green
& .\.venv\Scripts\Activate.ps1

# 4. Install dependencies
Write-Host "[*] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
python -m pip install --upgrade pip
pip install -r requirements.txt

# 5. Run setup environment bootstrap
Write-Host "[*] Running bootstrap verification..." -ForegroundColor Green
python setup_environment.py
