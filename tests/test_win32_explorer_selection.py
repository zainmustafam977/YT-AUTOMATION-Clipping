"""
Unit tests for Canonical Win32 File Explorer Selection (Task 1).
Verifies:
1. Canonical explorer.exe /select,"<normpath>" invocation with strict quoting and backslashes.
2. Proper handling of paths with spaces.
3. Non-ASCII and Unicode paths.
4. Forward slash normalization to backslashes on Windows.
5. Missing file returns structured fallback response.
6. Subprocess launch exceptions return structured fallback response.
7. Cross-platform handling (Darwin, Linux).
8. Server-side DB clip lookup.
9. FastAPI endpoints: canonical /api/clips/{id}/open-folder and deprecated alias /api/clip/{id}/open-folder.
"""

import os
import logging
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.auth import require_viewer
from shorts_engine.dashboard.routes.queue import get_db
from shorts_engine.db.models import Clip, User, UserRole


def test_explorer_selection_canonical_cmd(tmp_path):
    """Verifies that on Windows, open_in_folder invokes explorer.exe /select,"<normpath>" with shell=False."""
    test_file = tmp_path / "rendered_clip.mp4"
    test_file.write_bytes(b"video_bytes")

    with patch("platform.system", return_value="Windows"), \
         patch("sys.platform", "win32"), \
         patch("subprocess.Popen") as mock_popen:

        res = ClipRecoveryService.open_in_folder(test_file)

        assert res["status"] == "success"
        assert "Selected" in res["message"]
        expected_norm = os.path.normpath(str(test_file.resolve()))
        assert res["path"] == expected_norm
        mock_popen.assert_called_once()
        called_cmd, called_kwargs = mock_popen.call_args[0][0], mock_popen.call_args[1]
        assert called_cmd == f'explorer.exe /select,"{expected_norm}"'
        assert called_kwargs.get("shell") is False


def test_explorer_selection_with_spaces(tmp_path):
    """Verifies that paths with spaces are properly enclosed in quotes."""
    test_file = tmp_path / "rendered clip with multiple spaces.mp4"
    test_file.write_bytes(b"content")

    with patch("platform.system", return_value="Windows"), \
         patch("sys.platform", "win32"), \
         patch("subprocess.Popen") as mock_popen:

        res = ClipRecoveryService.open_in_folder(str(test_file))

        assert res["status"] == "success"
        expected_norm = os.path.normpath(str(test_file.resolve()))
        assert res["path"] == expected_norm
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == f'explorer.exe /select,"{expected_norm}"'


def test_explorer_selection_unicode_characters(tmp_path):
    """Verifies that paths with Unicode / emojis are properly resolved and normalized."""
    test_file = tmp_path / "clip_🔥_viral_日本語.mp4"
    test_file.write_bytes(b"unicode_data")

    with patch("platform.system", return_value="Windows"), \
         patch("sys.platform", "win32"), \
         patch("subprocess.Popen") as mock_popen:

        res = ClipRecoveryService.open_in_folder(test_file)

        assert res["status"] == "success"
        expected_norm = os.path.normpath(str(test_file.resolve()))
        assert res["path"] == expected_norm
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == f'explorer.exe /select,"{expected_norm}"'


def test_explorer_selection_forward_slashes_normalized(tmp_path):
    """Verifies forward slashes are strictly converted to OS backslashes."""
    sub_dir = tmp_path / "sub" / "folder"
    sub_dir.mkdir(parents=True)
    test_file = sub_dir / "clip.mp4"
    test_file.write_bytes(b"content")

    # Pass forward-slash string
    fwd_path = str(test_file.resolve()).replace("\\", "/")

    with patch("platform.system", return_value="Windows"), \
         patch("sys.platform", "win32"), \
         patch("subprocess.Popen") as mock_popen:

        res = ClipRecoveryService.open_in_folder(fwd_path)

        assert res["status"] == "success"
        expected_norm = os.path.normpath(str(test_file.resolve()))
        assert res["path"] == expected_norm
        mock_popen.assert_called_once()
        assert mock_popen.call_args[0][0] == f'explorer.exe /select,"{expected_norm}"'


def test_explorer_selection_missing_file_returns_fallback(tmp_path):
    """Verifies that if target file is missing, structured fallback response is returned."""
    missing_file = tmp_path / "non_existent_clip.mp4"

    res = ClipRecoveryService.open_in_folder(missing_file)

    assert res["status"] == "fallback"
    assert "Rendered file not found" in res["message"]
    assert res["path"] == os.path.normpath(str(missing_file.resolve()))
    assert res["folder"] == os.path.normpath(str(tmp_path.resolve()))


def test_explorer_selection_popen_exception_returns_fallback(tmp_path):
    """Verifies that if launcher raises an exception, structured fallback response is returned."""
    test_file = tmp_path / "test.mp4"
    test_file.write_bytes(b"data")

    with patch("platform.system", return_value="Windows"), \
         patch("sys.platform", "win32"), \
         patch("subprocess.Popen", side_effect=OSError("Access denied")):

        res = ClipRecoveryService.open_in_folder(test_file)

        assert res["status"] == "fallback"
        assert "Could not launch file manager" in res["message"]
        assert res["path"] == os.path.normpath(str(test_file.resolve()))


def test_explorer_selection_cross_platform_darwin_linux(tmp_path):
    """Verifies open_in_folder handles macOS and Linux cleanly."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"vid")

    # macOS
    with patch("platform.system", return_value="Darwin"), \
         patch("sys.platform", "darwin"), \
         patch("subprocess.Popen") as mock_popen:
        res = ClipRecoveryService.open_in_folder(clip)
        assert res["status"] == "success"
        assert "Finder" in res["message"]
        mock_popen.assert_called_once_with(["open", "-R", os.path.normpath(str(clip.resolve()))])

    # Linux
    with patch("platform.system", return_value="Linux"), \
         patch("sys.platform", "linux"), \
         patch("subprocess.Popen") as mock_popen:
        res = ClipRecoveryService.open_in_folder(clip)
        assert res["status"] == "success"
        assert "Opened folder" in res["message"]
        mock_popen.assert_called_once_with(["xdg-open", os.path.normpath(str(clip.parent.resolve()))])


def test_explorer_selection_db_clip_lookup(tmp_path):
    """Verifies that open_in_folder resolves clip path from database when passed Clip or ID."""
    test_video = tmp_path / "db_clip.mp4"
    test_video.write_bytes(b"content")

    clip = Clip(id="clip-db-777", rendered_path=str(test_video))
    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = clip

    with patch("platform.system", return_value="Windows"), \
         patch("sys.platform", "win32"), \
         patch("subprocess.Popen") as mock_popen:

        # 1. Test passing Clip model object directly
        res_clip = ClipRecoveryService.open_in_folder(clip)
        assert res_clip["status"] == "success"
        assert res_clip["path"] == os.path.normpath(str(test_video.resolve()))

        # 2. Test passing clip ID string with db session
        res_id = ClipRecoveryService.open_in_folder("clip-db-777", db=mock_db)
        assert res_id["status"] == "success"
        assert res_id["path"] == os.path.normpath(str(test_video.resolve()))


def test_api_endpoints_canonical_and_deprecated(tmp_path):
    """Verifies /api/clips/{id}/open-folder canonical and /api/clip/{id}/open-folder alias."""
    test_video = tmp_path / "rendered_test.mp4"
    test_video.write_bytes(b"dummy_video")

    fake_clip = MagicMock(spec=Clip)
    fake_clip.id = "test-clip-123"
    fake_clip.rendered_path = str(test_video)

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = fake_clip

    fake_user = User(id="u1", username="test_operator", role=UserRole.OPERATOR)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_viewer] = lambda: fake_user

    client = TestClient(app)

    try:
        with patch("shorts_engine.services.quality_gate.recovery.ClipRecoveryService.open_in_folder") as mock_open:
            mock_open.return_value = {
                "status": "success",
                "message": "Opened folder",
                "path": str(test_video),
                "folder": str(tmp_path),
            }

            # 1. Canonical route
            res_canon = client.post("/api/clips/test-clip-123/open-folder")
            assert res_canon.status_code == 200
            assert res_canon.json()["status"] == "success"

            # 2. Deprecated alias route
            with patch.object(logging.getLogger("shorts_engine.dashboard.routes.queue"), "warning") as mock_warn:
                res_depr = client.post("/api/clip/test-clip-123/open-folder")
                assert res_depr.status_code == 200
                assert res_depr.json()["status"] == "success"
                mock_warn.assert_called_once()
                assert "Deprecated route accessed" in mock_warn.call_args[0][0]

            # 3. Non-existent clip
            mock_db.query.return_value.filter_by.return_value.first.return_value = None
            res_404 = client.post("/api/clips/missing-clip/open-folder")
            assert res_404.status_code == 404

    finally:
        app.dependency_overrides.clear()
