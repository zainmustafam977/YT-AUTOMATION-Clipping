"""R4-1.6 — msvcrt cross-process lock tests.

The watch-folder daemon uses ``msvcrt.locking`` for cross-process
locking on Windows. This test exercises two processes contending for
the same lock region of a file and asserts they serialise.
"""
import os
import sys
import tempfile
import subprocess
import textwrap
import time
import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only: msvcrt is a Windows builtin",
)


def test_two_processes_serialise_on_lock():
    """Process B blocks while Process A holds the lock; A releases; B acquires."""
    fd, path = tempfile.mkstemp(prefix="yt-msvcrt-")
    os.close(fd)
    try:
        # Run two child processes that each take the lock for ~1s.
        script = textwrap.dedent(f"""
            import msvcrt, time, sys
            path = {path!r}
            # Open the file and lock a 1-byte region.
            f = open(path, "r+b")
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            print("ACQUIRED", flush=True)
            time.sleep(1.0)
            f.close()
            print("RELEASED", flush=True)
        """)
        a = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        # Wait until A has acquired the lock.
        line = a.stdout.readline()
        assert line.strip() == "ACQUIRED", f"unexpected A output: {line!r}"

        # Start B. It should block on the lock.
        b = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        # Give B half a second — it should NOT have acquired yet.
        time.sleep(0.5)
        # Non-blocking read; should have nothing.
        import select
        r, _, _ = select.select([b.stdout], [], [], 0.0)
        assert r == [], "B acquired the lock before A released it"

        # Wait for A to release.
        a.wait(timeout=5)
        assert a.stdout.readline().strip() == "RELEASED"

        # Now B should acquire within 5s.
        b_out = b.stdout.readline()
        b.wait(timeout=5)
        assert b_out.strip() == "ACQUIRED", f"B never acquired: {b_out!r}"
        assert b.stdout.readline().strip() == "RELEASED"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
