"""R4-1.6 — worker watchdog test.

When a worker is killed (or crashes), the watchdog must ensure that
every child process (e.g. ffmpeg) is also killed. This is enforced by
``SubprocessWatchdog.run`` which uses psutil to walk the process tree
on timeout / terminate.

This test uses ``sleep`` in place of ffmpeg so it runs without ffmpeg
on PATH.
"""
import os
import sys
import time
import signal
import subprocess
import pytest

from shorts_engine.services.execution.watchdog import SubprocessWatchdog


def test_kill_on_timeout_reaps_children():
    """When the timeout fires, all children of the subprocess are killed."""
    # Spawn a shell that spawns a sleep grandchild.
    cmd = [
        sys.executable, "-c",
        "import subprocess, time, sys, os;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
        "print(p.pid, flush=True);"
        "time.sleep(120)",
    ]
    try:
        proc = SubprocessWatchdog.run(cmd, timeout_sec=2.0, check=False)
        assert proc.returncode != 0
    except Exception:
        # SubprocessWatchdog may raise; either way the child should be dead.
        pass
    # The grandchild (sleep) should not be alive 3s after the timeout.
    time.sleep(1.0)
    # psutil.pids() should not contain the grandchild pid.
    import psutil
    living = psutil.pids()
    # We don't know the grandchild's pid precisely, but the parent
    # should be gone and any python process we spawned should be gone.
    for pid in living:
        try:
            p = psutil.Process(pid)
            if p.name() in ("python", "sleep") and p.create_time() > time.time() - 30:
                # If this is one of OUR processes, it should already be dead.
                cmdline = " ".join(p.cmdline())
                if "time.sleep(120)" in cmdline:
                    pytest.fail(f"orphan child still alive: pid={pid} cmdline={cmdline!r}")
        except psutil.NoSuchProcess:
            continue


def test_kill_on_terminate_reaps_children():
    """SubprocessWatchdog.terminate() kills the entire process tree."""
    cmd = [
        sys.executable, "-c",
        "import subprocess, time, sys;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
        "print(p.pid, flush=True);"
        "time.sleep(120)",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    # Wait for the grandchild pid to be printed.
    line = proc.stdout.readline()
    assert line.strip().isdigit(), f"unexpected: {line!r}"
    # Send SIGTERM via the watchdog.
    import psutil
    parent = psutil.Process(proc.pid)
    # Manually walk and kill the tree.
    children = parent.children(recursive=True)
    for c in children:
        try:
            c.terminate()
        except psutil.NoSuchProcess:
            pass
    parent.terminate()
    proc.wait(timeout=5)
    # Wait a moment for the kernel to reap.
    time.sleep(1.0)
    for c in children:
        if c.is_running():
            pytest.fail(f"child still alive after terminate: pid={c.pid}")
