"""R4-1.6 — pywin32 job-object tests.

These tests require Windows. On any other platform they are skipped.

The test uses ``win32job`` (from pywin32) to create a job object,
assigns the spawned ffmpeg-equivalent process to it, and asserts the
child is reaped when the parent is killed.

A ``sleep`` process is used in place of ffmpeg so the test does not
require ffmpeg on PATH.
"""
import sys
import time
import subprocess
import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only: requires pywin32 + job objects",
)


def _have_pywin32():
    try:
        import win32job  # noqa: F401
        import win32process  # noqa: F401
        import win32api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _have_pywin32(),
    reason="pywin32 not installed",
)


def test_child_killed_when_parent_killed():
    """A child assigned to a job object dies with the parent."""
    import win32job
    import win32process
    import win32api
    import win32con

    # Create the job.
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation,
    )
    info["BasicLimitInformation"]["LimitFlags"] = (
        win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    win32job.SetInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation, info,
    )

    # Spawn a long-running child.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
    )
    try:
        # Assign the child to the job.
        handle = win32api.OpenProcess(
            win32con.PROCESS_ALL_ACCESS, False, child.pid,
        )
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()

        # Confirm child is alive.
        assert child.poll() is None

        # Close the job handle — this should kill the child.
        win32api.CloseHandle(job)

        # Wait for the child to die.
        deadline = time.time() + 5
        while time.time() < deadline:
            if child.poll() is not None:
                break
            time.sleep(0.1)

        assert child.poll() is not None, "child still alive 5s after job close"
    finally:
        if child.poll() is None:
            child.kill()


def test_concurrent_children_all_killed():
    """Multiple children of the same job die together."""
    import win32job
    import win32api
    import win32con

    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation,
    )
    info["BasicLimitInformation"]["LimitFlags"] = (
        win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    win32job.SetInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation, info,
    )

    children = []
    for _ in range(3):
        c = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
        )
        h = win32api.OpenProcess(
            win32con.PROCESS_ALL_ACCESS, False, c.pid,
        )
        try:
            win32job.AssignProcessToJobObject(job, h)
        finally:
            h.Close()
        children.append(c)

    try:
        for c in children:
            assert c.poll() is None

        win32api.CloseHandle(job)

        deadline = time.time() + 5
        while time.time() < deadline:
            if all(c.poll() is not None for c in children):
                break
            time.sleep(0.1)

        for c in children:
            assert c.poll() is not None, "child still alive after job close"
    finally:
        for c in children:
            if c.poll() is None:
                c.kill()
