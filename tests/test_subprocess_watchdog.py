import sys
import time
import pytest
from shorts_engine.services.execution.watchdog import (
    SubprocessWatchdog,
    SubprocessTimeoutError,
    SubprocessExecutionError,
)


def test_watchdog_successful_execution():
    """Verifies standard clean command execution returns exit code 0."""
    res = SubprocessWatchdog.run([sys.executable, "-c", "print('watchdog_ok')"], timeout_sec=10.0)
    assert res.is_success
    assert res.exit_code == 0
    assert "watchdog_ok" in res.stdout
    assert res.duration_ms > 0


def test_watchdog_hard_timeout_and_tree_kill(tmp_path):
    """Verifies that a hanging subprocess is killed upon timeout and SubprocessTimeoutError is raised."""
    dummy_file = tmp_path / "should_be_cleaned.mp4"
    dummy_file.write_text("partial corrupted video data", encoding="utf-8")
    assert dummy_file.exists()

    cmd = [sys.executable, "-c", "import time; time.sleep(15)"]
    start = time.monotonic()

    with pytest.raises(SubprocessTimeoutError) as exc_info:
        SubprocessWatchdog.run(
            cmd,
            timeout_sec=0.5,
            artifacts_to_cleanup=[dummy_file],
        )

    elapsed = time.monotonic() - start
    assert elapsed < 3.0  # Must be killed quickly, not wait 15 seconds
    assert not dummy_file.exists()  # Incomplete artifact must be purged
    assert "exceeded hard timeout" in str(exc_info.value)


def test_watchdog_nonzero_exit_code_and_cleanup(tmp_path):
    """Verifies that non-zero exit code raises SubprocessExecutionError and cleans up artifacts."""
    dummy_file = tmp_path / "broken_render.mp4"
    dummy_file.write_text("broken render", encoding="utf-8")

    cmd = [sys.executable, "-c", "import sys; sys.stderr.write('fatal crash\\n'); sys.exit(42)"]

    with pytest.raises(SubprocessExecutionError) as exc_info:
        SubprocessWatchdog.run(
            cmd,
            timeout_sec=5.0,
            check=True,
            artifacts_to_cleanup=[dummy_file],
        )

    assert exc_info.value.exit_code == 42
    assert "fatal crash" in exc_info.value.stderr
    assert not dummy_file.exists()


def test_watchdog_check_false_returns_result():
    """Verifies check=False returns ExecutionResult without raising on non-zero exit."""
    cmd = [sys.executable, "-c", "import sys; sys.exit(7)"]
    res = SubprocessWatchdog.run(cmd, timeout_sec=5.0, check=False)
    assert not res.is_success
    assert res.exit_code == 7
