"""
Unit and integration tests for Watched Folder Daemon (Task 2.4).
Verifies:
1. Win32 file-lock probe detection (is_file_locked).
2. Video candidate filtering and extension whitelisting.
3. Multi-second byte-stability verification (preventing partial ingest).
4. Processing callback dispatch and EventBus alerting.
5. Clean start/stop thread lifecycle and context manager support.
"""

import time
import pytest
from pathlib import Path

from shorts_engine.services.ingestion.watch_folder import WatchFolderDaemon, is_file_locked
from shorts_engine.services.events.bus import global_event_bus


@pytest.fixture(autouse=True)
def clean_events():
    global_event_bus.clear()
    yield
    global_event_bus.clear()


def test_is_file_locked_on_unlocked_file(tmp_path):
    """Verifies is_file_locked returns False on a normal closed file."""
    f = tmp_path / "closed.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
    assert is_file_locked(f) is False


def test_is_file_locked_on_nonexistent_file(tmp_path):
    """Verifies is_file_locked returns True if file does not exist."""
    assert is_file_locked(tmp_path / "missing.mp4") is True


def test_is_file_locked_when_open_for_writing(tmp_path):
    """Verifies is_file_locked detects an actively open file handle."""
    target = tmp_path / "writing.mp4"
    with open(target, "wb") as f_out:
        f_out.write(b"in-flight-stream-bytes")
        f_out.flush()
        # While handle is held open, lock check should detect activity
        # (On Windows, opening with write mode prevents unshared write acquisition)
        # Note: on POSIX or Windows, verify behavior:
        locked = is_file_locked(target)
        # In Windows with CreateFileW, this is guaranteed True
        assert locked is True

    # Once closed, lock check should return False
    assert is_file_locked(target) is False


def test_watch_folder_candidate_registration(tmp_path):
    """Verifies daemon registers video extensions and ignores other files."""
    daemon = WatchFolderDaemon(watch_dir=tmp_path, stable_time_sec=1.0)

    # Valid extension
    daemon.register_candidate(str(tmp_path / "clip.mp4"))
    assert len(daemon._pending_files) == 1

    # Invalid extension
    daemon.register_candidate(str(tmp_path / "thumbnail.jpg"))
    daemon.register_candidate(str(tmp_path / "subtitles.srt"))
    assert len(daemon._pending_files) == 1


def test_stability_timer_and_callback_dispatch(tmp_path):
    """
    Verifies that files must remain size-stable for stable_time_sec
    before being dispatched to processing callback.
    """
    processed = []

    def on_ready(path: Path):
        processed.append(path)

    # Short stability window for fast test
    daemon = WatchFolderDaemon(
        watch_dir=tmp_path,
        callback=on_ready,
        stable_time_sec=0.5,
        poll_interval_sec=0.1,
        auto_ingest_db=False,
    )

    test_file = tmp_path / "stabilizing_video.mp4"
    test_file.write_bytes(b"initial_bytes" * 50)

    daemon.register_candidate(str(test_file))

    # First immediate poll -> Records initial size, but elapsed time < 0.5s
    ready1 = daemon.poll_and_process_stable_files()
    assert len(ready1) == 0
    assert len(processed) == 0

    # Wait for stability window to elapse
    time.sleep(0.6)

    # Second poll -> Elapsed time >= 0.5s and size unchanged -> Dispatches!
    ready2 = daemon.poll_and_process_stable_files()
    assert len(ready2) == 1
    assert ready2[0].name == "stabilizing_video.mp4"
    assert len(processed) == 1

    # Check EventBus notification
    events = global_event_bus.get_history(category="ingestion")
    assert len(events) >= 1
    assert "Watch Folder Video Detected" in events[-1].title

    # Third poll -> File already processed, should not re-trigger
    ready3 = daemon.poll_and_process_stable_files()
    assert len(ready3) == 0


def test_size_mutation_resets_stability_window(tmp_path):
    """Verifies that modifying file size during stabilization resets the timer."""
    processed = []
    daemon = WatchFolderDaemon(
        watch_dir=tmp_path,
        callback=lambda p: processed.append(p),
        stable_time_sec=0.4,
        poll_interval_sec=0.1,
        auto_ingest_db=False,
    )

    test_file = tmp_path / "downloading.mp4"
    test_file.write_bytes(b"chunk1" * 10)
    daemon.register_candidate(str(test_file))

    daemon.poll_and_process_stable_files()

    # Sleep 0.25s (less than 0.4s) and append more data
    time.sleep(0.25)
    with open(test_file, "ab") as f:
        f.write(b"chunk2" * 20)

    # Poll: should notice size changed and reset timer
    daemon.poll_and_process_stable_files()
    assert len(processed) == 0

    # Sleep another 0.25s: total time elapsed > 0.4s from start, but < 0.4s from last size mutation
    time.sleep(0.25)
    daemon.poll_and_process_stable_files()
    assert len(processed) == 0

    # Sleep another 0.25s: now stable for > 0.4s after mutation
    time.sleep(0.25)
    daemon.poll_and_process_stable_files()
    assert len(processed) == 1


def test_daemon_lifecycle_start_stop(tmp_path):
    """Verifies clean daemon start, thread execution, and stop."""
    daemon = WatchFolderDaemon(watch_dir=tmp_path, poll_interval_sec=0.1)
    assert daemon.is_running is False

    daemon.start()
    assert daemon.is_running is True

    time.sleep(0.3)
    daemon.stop()
    assert daemon.is_running is False


def test_daemon_context_manager(tmp_path):
    """Verifies context manager entry and exit."""
    with WatchFolderDaemon(watch_dir=tmp_path, poll_interval_sec=0.1) as daemon:
        assert daemon.is_running is True
    assert daemon.is_running is False


def test_is_file_locked_on_readonly_file(tmp_path):
    """Verifies that a closed read-only file is recognized as UNLOCKED."""
    import stat
    p = tmp_path / "readonly_video.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
    try:
        p.chmod(stat.S_IREAD)
        assert is_file_locked(p) is False
    finally:
        p.chmod(stat.S_IWRITE)


def test_lock_release_resets_stability_window(tmp_path):
    """Verifies that the stability timer only begins AFTER an active lock is fully released."""
    processed = []
    daemon = WatchFolderDaemon(
        watch_dir=tmp_path,
        callback=lambda p: processed.append(p),
        stable_time_sec=0.4,
        poll_interval_sec=0.05,
        auto_ingest_db=False,
    )

    test_file = tmp_path / "in_flight.mp4"
    # Open for writing and keep open
    f_out = open(test_file, "wb")
    f_out.write(b"data" * 50)
    f_out.flush()

    daemon.register_candidate(str(test_file))

    # Poll while locked across 0.5s (> stable_time_sec)
    for _ in range(5):
        time.sleep(0.1)
        daemon.poll_and_process_stable_files()
        assert len(processed) == 0

    # Close file lock now
    f_out.close()

    # Immediate poll right after lock release -> should NOT dispatch yet because stability window was reset
    daemon.poll_and_process_stable_files()
    assert len(processed) == 0

    # Wait for full stability window (0.45s) after lock release
    time.sleep(0.45)
    daemon.poll_and_process_stable_files()
    assert len(processed) == 1
    assert processed[0].name == "in_flight.mp4"


def test_redropped_file_reingestion_after_deletion(tmp_path):
    """Verifies that deleting a processed file from disk clears processed cache and allows re-dropping."""
    processed = []
    daemon = WatchFolderDaemon(
        watch_dir=tmp_path,
        callback=lambda p: processed.append(p),
        stable_time_sec=0.2,
        poll_interval_sec=0.05,
        auto_ingest_db=False,
    )

    f = tmp_path / "drop.mp4"
    f.write_bytes(b"initial_bytes" * 20)
    daemon.register_candidate(str(f))

    # Baseline poll establishes initial size and starts stability window
    daemon.poll_and_process_stable_files()
    time.sleep(0.25)
    daemon.poll_and_process_stable_files()
    assert len(processed) == 1

    # Delete the file from disk
    f.unlink()
    # Next poll cleans up processed tracking
    daemon.poll_and_process_stable_files()

    # Re-drop file with same name
    f.write_bytes(b"new_content" * 20)
    daemon.register_candidate(str(f))

    # Baseline poll establishes initial size
    daemon.poll_and_process_stable_files()
    time.sleep(0.25)
    daemon.poll_and_process_stable_files()
    assert len(processed) == 2
