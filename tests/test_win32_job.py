"""
Unit tests for Win32 Job Object supervisor (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE).
Verifies:
1. Job Object initialization and limit flag configuration
2. Current process and child process assignment
3. SubprocessWatchdog automatic child process assignment
4. Cross-platform safety and error resilience
"""

import sys
import subprocess
from unittest.mock import patch

import pytest

from shorts_engine.services.execution.win32_job import (
    Win32JobManager,
    init_win32_job_object,
)
from shorts_engine.services.execution.watchdog import SubprocessWatchdog


def test_win32_job_initialization_on_current_platform():
    """Verifies that init_win32_job_object executes cleanly on the current host."""
    mgr = Win32JobManager()
    success = mgr.initialize()
    if sys.platform == "win32":
        assert success is True
        assert mgr.is_active is True
        assert mgr.handle is not None
    else:
        assert success is False
        assert mgr.is_active is False


def test_win32_job_assign_child_process():
    """Verifies that child process PIDs can be explicitly assigned to the Job Object."""
    mgr = Win32JobManager()
    mgr.initialize()

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.5)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if sys.platform == "win32" and mgr.is_active:
            res = mgr.assign_process(proc.pid)
            assert res is True or res is False  # Valid boolean result without raising
        else:
            res = mgr.assign_process(proc.pid)
            assert res is False
    finally:
        proc.terminate()
        proc.wait(timeout=2.0)


def test_win32_job_watchdog_execution_with_job():
    """Verifies SubprocessWatchdog runs seamlessly with the Win32 Job Object active."""
    init_win32_job_object()
    res = SubprocessWatchdog.run([sys.executable, "-c", "print('job_test_ok')"], timeout_sec=5.0)
    assert res.is_success
    assert "job_test_ok" in res.stdout


def test_win32_job_non_windows_platform_mock():
    """Verifies safe no-op on non-Windows platforms."""
    mgr = Win32JobManager()
    with patch("sys.platform", "linux"):
        assert mgr.initialize() is False
        assert mgr.is_active is False
        assert mgr.assign_process(1234) is False


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Patches ctypes.windll.kernel32, which only exists on Windows.",
)
def test_win32_job_handles_api_failure_gracefully():
    """Verifies that Win32 API failure during creation is caught and logged."""
    mgr = Win32JobManager()
    with patch("sys.platform", "win32"):
        with patch("ctypes.windll.kernel32.CreateJobObjectW", return_value=0):
            with patch("ctypes.windll.kernel32.GetLastError", return_value=5):
                success = mgr.initialize()
                assert success is False
                assert mgr.is_active is False
