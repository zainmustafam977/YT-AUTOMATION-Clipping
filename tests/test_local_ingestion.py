"""
Tests for Universal Local Ingestion & Hotfolder Watcher Service (Pillar 4).
Verifies:
1. Win32 file-lock detection and process lock contention.
2. Byte stability window verification for in-flight file drops.
3. Automated hotfolder scanning, zero-copy DB registration, and processed folder movement.
4. Preview proxy necessity detection for incompatible codecs (HEVC, ProRes, AV1, MKV).
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

from shorts_engine.services.ingestion.watch_folder import is_file_locked
from shorts_engine.services.ingestion.hotfolder_watcher import HotfolderWatcher
from shorts_engine.services.ingestion.local_loader import (
    LocalVideoIngestionService,
    is_preview_proxy_needed,
)
from shorts_engine.db.models import SourceVideo


# ---------------------------------------------------------------------------
# Win32 & POSIX File-Lock Tests
# ---------------------------------------------------------------------------

def test_is_file_locked_normal_file(tmp_path):
    """A closed file on disk must report is_file_locked as False."""
    test_file = tmp_path / "sample.mp4"
    test_file.write_bytes(b"dummy video data")
    assert is_file_locked(test_file) is False


def test_is_file_locked_nonexistent_file(tmp_path):
    """Non-existent file reports True (not safe to read/process)."""
    missing_file = tmp_path / "non_existent.mp4"
    assert is_file_locked(missing_file) is True


def test_is_file_locked_mock_sharing_violation(tmp_path):
    """When OS reports ERROR_SHARING_VIOLATION (32), is_file_locked returns True."""
    test_file = tmp_path / "locked.mp4"
    test_file.write_bytes(b"in flight bytes")

    with patch("ctypes.windll.kernel32.CreateFileW", return_value=-1):
        with patch("ctypes.windll.kernel32.GetLastError", return_value=32):
            assert is_file_locked(test_file) is True


def test_is_file_locked_posix_permission_error(tmp_path):
    """Simulates fallback when open() encounters a PermissionError (locked)."""
    test_file = tmp_path / "busy.mp4"
    test_file.write_bytes(b"content")

    # Patch platform to non-win32 to test the fallback branch
    with patch("sys.platform", "linux"):
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            assert is_file_locked(test_file) is True


# ---------------------------------------------------------------------------
# Hotfolder Watcher Pending Files & Filtering
# ---------------------------------------------------------------------------

def test_hotfolder_get_pending_files(tmp_path):
    """Hotfolder pending scan discovers supported formats and ignores subdirs/unsupported types."""
    hot_dir = tmp_path / "hotfolder"
    hot_dir.mkdir()

    # Supported files
    (hot_dir / "vid1.mp4").write_bytes(b"data1")
    (hot_dir / "vid2.mov").write_bytes(b"data2")
    (hot_dir / "vid3.mkv").write_bytes(b"data3")
    (hot_dir / "vid4.webm").write_bytes(b"data4")

    # Unsupported files
    (hot_dir / "notes.txt").write_text("info")
    (hot_dir / "image.jpg").write_bytes(b"img")

    # Subdirectory and processed file
    sub = hot_dir / "nested"
    sub.mkdir()
    (sub / "nested.mp4").write_bytes(b"nested")

    watcher = HotfolderWatcher(folder_path=hot_dir, stability_window_sec=0.01)
    pending = watcher.get_pending_files()

    pending_names = [p.name for p in pending]
    assert sorted(pending_names) == ["vid1.mp4", "vid2.mov", "vid3.mkv", "vid4.webm"]


# ---------------------------------------------------------------------------
# Byte-Stability Verification Tests
# ---------------------------------------------------------------------------

def test_hotfolder_is_file_ready_empty_file(tmp_path):
    """0-byte file drop is not ready."""
    empty_file = tmp_path / "zero.mp4"
    empty_file.touch()

    watcher = HotfolderWatcher(folder_path=tmp_path, stability_window_sec=0.01)
    assert watcher.is_file_ready(empty_file) is False


def test_hotfolder_is_file_ready_locked_file(tmp_path):
    """File detected as locked by another process is not ready."""
    file = tmp_path / "transferring.mp4"
    file.write_bytes(b"data")

    watcher = HotfolderWatcher(folder_path=tmp_path, stability_window_sec=0.01)
    with patch("shorts_engine.services.ingestion.hotfolder_watcher.is_file_locked", return_value=True):
        assert watcher.is_file_ready(file) is False


def test_hotfolder_is_file_ready_unstable_size(tmp_path):
    """File whose size changes across the stability window is rejected as still writing."""
    file = tmp_path / "growing.mp4"
    file.write_bytes(b"data")

    watcher = HotfolderWatcher(folder_path=tmp_path, stability_window_sec=0.01)

    real_mode = file.stat().st_mode
    mock_stat1 = MagicMock(st_size=100, st_mode=real_mode)
    mock_stat2 = MagicMock(st_size=250, st_mode=real_mode)

    with patch.object(Path, "stat", side_effect=[mock_stat1, mock_stat2]):
        with patch("shorts_engine.services.ingestion.hotfolder_watcher.is_file_locked", return_value=False):
            with patch("time.sleep"):  # Avoid test delay
                assert watcher.is_file_ready(file) is False


def test_hotfolder_is_file_ready_stable(tmp_path):
    """File with stable size and no lock is marked ready."""
    file = tmp_path / "completed.mp4"
    file.write_bytes(b"complete binary content")

    watcher = HotfolderWatcher(folder_path=tmp_path, stability_window_sec=0.01)
    with patch("time.sleep"):
        assert watcher.is_file_ready(file) is True


def test_hotfolder_is_file_ready_deleted_during_window(tmp_path):
    """File deleted during the stability window returns False gracefully."""
    file = tmp_path / "deleted.mp4"
    file.write_bytes(b"temporary")

    watcher = HotfolderWatcher(folder_path=tmp_path, stability_window_sec=0.01)

    # First exists() call True, second False
    with patch.object(Path, "exists", side_effect=[True, False]):
        with patch("shorts_engine.services.ingestion.hotfolder_watcher.is_file_locked", return_value=False):
            with patch("time.sleep"):
                assert watcher.is_file_ready(file) is False


# ---------------------------------------------------------------------------
# Hotfolder Auto-Ingestion & Processed Folder Movement
# ---------------------------------------------------------------------------

def test_hotfolder_scan_once_auto_ingest_and_move(tmp_path):
    """scan_once stabilizes, ingests via LocalVideoIngestionService, and moves file to processed/."""
    hot_dir = tmp_path / "hotfolder"
    hot_dir.mkdir()
    video_file = hot_dir / "podcast_ep42.mp4"
    video_file.write_bytes(b"valid test video bits")

    mock_sv = MagicMock(spec=SourceVideo)
    mock_sv.id = "sv-uuid-42"
    mock_sv.title = "Podcast Ep42"
    mock_sv.duration_sec = 120
    mock_sv.downloaded_path = str(video_file)

    watcher = HotfolderWatcher(folder_path=hot_dir, stability_window_sec=0.01)

    mock_session = MagicMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__.return_value = mock_session
    mock_session_ctx.__exit__.return_value = None

    with patch("shorts_engine.services.ingestion.hotfolder_watcher.SessionLocal", return_value=mock_session_ctx):
        with patch.object(LocalVideoIngestionService, "ingest_local_path", return_value=mock_sv) as mock_ingest:
            with patch("time.sleep"):
                ingested = watcher.scan_once()

    assert len(ingested) == 1
    assert ingested[0] == mock_sv

    # Ingest service was called
    mock_ingest.assert_called_once()

    # Original file is moved out of hotfolder root
    assert not video_file.exists()

    # Destination file exists in processed subdirectory
    expected_processed = hot_dir / "processed" / "podcast_ep42.mp4"
    assert expected_processed.exists()
    assert expected_processed.read_bytes() == b"valid test video bits"

    # SourceVideo downloaded_path was updated
    assert mock_sv.downloaded_path == str(expected_processed)

    # Next scan finds nothing pending
    assert len(watcher.scan_once()) == 0


def test_hotfolder_scan_once_collision_avoidance(tmp_path):
    """When a file with the same name already exists in processed/, a timestamp prefix is added."""
    hot_dir = tmp_path / "hotfolder"
    hot_dir.mkdir()
    processed_dir = hot_dir / "processed"
    processed_dir.mkdir()

    # Pre-existing file in processed
    (processed_dir / "clip.mp4").write_bytes(b"old clip")

    # New drop with same name
    new_drop = hot_dir / "clip.mp4"
    new_drop.write_bytes(b"new clip")

    mock_sv = MagicMock(spec=SourceVideo)
    mock_sv.id = "sv-new"
    mock_sv.title = "Clip"
    mock_sv.duration_sec = 30
    mock_sv.downloaded_path = str(new_drop)

    watcher = HotfolderWatcher(folder_path=hot_dir, stability_window_sec=0.01)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__.return_value = MagicMock()
    mock_session_ctx.__exit__.return_value = None

    with patch("shorts_engine.services.ingestion.hotfolder_watcher.SessionLocal", return_value=mock_session_ctx):
        with patch.object(LocalVideoIngestionService, "ingest_local_path", return_value=mock_sv):
            with patch("time.sleep"):
                with patch("time.time", return_value=1700000000):
                    ingested = watcher.scan_once()

    assert len(ingested) == 1
    # Old file intact
    assert (processed_dir / "clip.mp4").read_bytes() == b"old clip"
    # New file saved with timestamp prefix
    expected_collision_path = processed_dir / "1700000000_clip.mp4"
    assert expected_collision_path.exists()
    assert expected_collision_path.read_bytes() == b"new clip"


def test_hotfolder_status_overview(tmp_path):
    """Watcher get_status returns folder diagnostic statistics."""
    hot_dir = tmp_path / "hotfolder"
    hot_dir.mkdir()
    (hot_dir / "test1.mp4").write_bytes(b"data")

    watcher = HotfolderWatcher(folder_path=hot_dir, stability_window_sec=0.01)
    status = watcher.get_status()

    assert status["enabled"] is True
    assert status["path"] == str(hot_dir.resolve())
    assert status["pending_files_count"] == 1
    assert status["processed_files_count"] == 0
    assert ".mp4" in status["supported_extensions"]


# ---------------------------------------------------------------------------
# Preview Proxy Necessity Detection (Enhancement 4)
# ---------------------------------------------------------------------------

def test_is_preview_proxy_needed():
    """Identifies browser-incompatible containers and codecs requiring preview transcoding."""
    # .mkv container always needs proxy for HTML5 video tag
    assert is_preview_proxy_needed("footage.mkv") is True
    assert is_preview_proxy_needed("path/to/clip.MKV") is True

    # Standard H.264 MP4 does not need proxy
    assert is_preview_proxy_needed("video.mp4", codec="h264") is False
    assert is_preview_proxy_needed("video.mp4", codec="avc1") is False

    # Incompatible codecs need proxy
    assert is_preview_proxy_needed("video.mp4", codec="hevc") is True
    assert is_preview_proxy_needed("video.mp4", codec="h265") is True
    assert is_preview_proxy_needed("video.mov", codec="prores") is True
    assert is_preview_proxy_needed("video.webm", codec="av1") is True
    assert is_preview_proxy_needed("video.mp4", codec="vp9") is True
