"""Fresh-eyes loop (R8.5) — run the full agent gate as a single test.

This test exercises every gate in CI:
  * ledger consistency
  * mypy ratchet
  * ruff (on the new code)
  * pytest
  * scale-1k
  * sabotage checks
  * E2E scenarios

If this test passes, the entire agent pipeline is green.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run(cmd: list, **kw):
    print(f"\n[FRESH-EYES] running: {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=REPO_ROOT, **kw)


def test_ledger_consistency():
    r = _run([PY, "tools/check_agent_ledger.py"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_mypy_ratchet():
    r = _run([PY, "tools/check_mypy_ratchet.py"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_ruff_on_new_code():
    r = _run(
        [sys.executable, "-m", "ruff", "check",
         "shorts_engine/services/abtesting/",
         "shorts_engine/services/webhooks/",
         "shorts_engine/services/settings_io/",
         "shorts_engine/services/cost/",
         "shorts_engine/services/captioning/",
         "shorts_engine/services/security/",
         "shorts_engine/services/batch/",
         "shorts_engine/dashboard/routes/batch.py",
         "tools/"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_pytest_sabotage_scale_e2e():
    """Run the three critical gates in one subprocess."""
    import glob
    sabotage = sorted(glob.glob("tests/test_sabotage_*.py"))
    r = _run(
        [sys.executable, "-m", "pytest", "-v",
         *sabotage,
         "tests/test_scale_1k_batch.py",
         "tests/e2e/test_s3_batch.py",
         "tests/e2e/test_s7_ab_test.py",
         "--tb=short", "-q"],
        capture_output=True, text=True,
        env={**os.environ, "DASHBOARD_SECRET_KEY": "test-key-1234567890",
             "ENCRYPTION_KEY": "test-fernet-key-1234567890"},
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
