#!/usr/bin/env bash
# ==============================================================================
# YouTube Shorts Automation - Automated Setup (Linux / macOS)
# ==============================================================================
set -e

echo "=============================================================================="
echo "        YouTube Shorts Automation - Automated Setup (Unix / macOS)"
echo "=============================================================================="
echo ""

# 1. Check Python installation
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "[ERROR] Python 3 is not installed or not in PATH."
    echo "Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    echo "macOS:         brew install python@3.11"
    exit 1
fi

# 2. Check virtual environment
if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment (.venv)..."
    $PYTHON_BIN -m venv .venv
fi

echo "[*] Activating virtual environment..."
source .venv/bin/activate

# 3. Upgrade pip and install dependencies
echo "[*] Installing dependencies from requirements.txt..."
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Run automated environment bootstrap
python setup_environment.py
