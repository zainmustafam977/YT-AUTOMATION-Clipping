"""
Unit tests for the Windows One-Click Self-Healing Bootstrapper.
Verifies:
1. run.bat exists, is valid batch script, and invokes bootstrap.ps1 with execution policy bypass.
2. scripts/bootstrap.ps1 exists and contains all 6 self-healing steps:
   - Python 3.11/3.12 detection and winget fallback
   - FFmpeg detection and BtbN GPL download fallback
   - Virtual environment (.venv) verification and creation
   - Alembic migration upgrade
   - Port 8000 conflict check and termination
   - Uvicorn app launch and browser trigger
"""

from pathlib import Path


def test_run_bat_structure():
    """Verifies that run.bat is a valid launcher invoking bootstrap.ps1."""
    project_root = Path(__file__).parent.parent
    run_bat = project_root / "run.bat"

    assert run_bat.exists(), "run.bat must exist in project root"
    content = run_bat.read_text(encoding="utf-8")

    assert "@echo off" in content
    assert "powershell.exe" in content
    assert "-ExecutionPolicy Bypass" in content
    assert "scripts\\bootstrap.ps1" in content or "scripts/bootstrap.ps1" in content


def test_bootstrap_ps1_self_healing_steps():
    """Verifies scripts/bootstrap.ps1 covers all required setup phases."""
    project_root = Path(__file__).parent.parent
    bootstrap_ps1 = project_root / "scripts" / "bootstrap.ps1"

    assert bootstrap_ps1.exists(), "scripts/bootstrap.ps1 must exist"
    content = bootstrap_ps1.read_text(encoding="utf-8")

    # Step 1: Python check
    assert "Checking Python Runtime" in content
    assert "3.11" in content
    assert "winget install" in content

    # Step 2: FFmpeg check
    assert "Checking FFmpeg" in content
    assert "ffmpeg.exe" in content
    assert "BtbN" in content

    # Step 3: Venv & dependencies
    assert ".venv" in content
    assert "pip install" in content

    # Step 4: Migrations
    assert "alembic" in content
    assert "upgrade head" in content

    # Step 5: Port 8000
    assert "8000" in content
    assert "Get-NetTCPConnection" in content

    # Step 6: Browser & Uvicorn
    assert "http://127.0.0.1:8000" in content
    assert "shorts_engine.dashboard.app:app" in content


def test_bootstrap_ps1_syntax_validity():
    """Verifies that scripts/bootstrap.ps1 contains valid PowerShell syntax without any parse errors."""
    import shutil
    import subprocess

    project_root = Path(__file__).parent.parent
    bootstrap_ps1 = project_root / "scripts" / "bootstrap.ps1"
    assert bootstrap_ps1.exists()

    powershell_bin = shutil.which("powershell") or shutil.which("pwsh")
    if powershell_bin:
        script_path_str = str(bootstrap_ps1).replace("'", "''")
        cmd = [
            powershell_bin,
            "-NoProfile",
            "-Command",
            f"$raw = Get-Content -LiteralPath '{script_path_str}' -Raw; [void][scriptblock]::Create($raw); Write-Output 'SYNTAX_OK'",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"PowerShell syntax check failed: {result.stderr}\n{result.stdout}"
        assert "SYNTAX_OK" in result.stdout

