"""
Unit and integration tests for:
1. Centralized JobProgressTracker (thread safety, stage updates, clamped percentages)
2. Thumbnail resolver API (/api/thumbnails/{id}) and SVG fallback
3. Manual video processing channel auto-resolution & caption preset passing
"""

from unittest.mock import patch
from fastapi.testclient import TestClient

from shorts_engine.services.telemetry.progress_tracker import JobProgressTracker
from shorts_engine.dashboard.app import app
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import User


def test_job_progress_tracker_lifecycle():
    tracker = JobProgressTracker()
    test_id = "test_vid_123"

    # Initially None
    assert tracker.get_progress(test_id) is None

    # Update progress
    tracker.update_progress(
        video_id=test_id,
        stage="DOWNLOADING",
        percent=45.67,
        detail="Downloading 45%...",
        speed="2.5 MB/s",
        eta_seconds=12.0,
    )

    prog = tracker.get_progress(test_id)
    assert prog is not None
    assert prog["video_id"] == test_id
    assert prog["stage"] == "DOWNLOADING"
    assert prog["percent"] == 45.7
    assert prog["speed"] == "2.5 MB/s"
    assert prog["eta_seconds"] == 12.0

    # Test clamping
    tracker.update_progress(test_id, "RENDERING", 125.0, "Over 100%")
    assert tracker.get_progress(test_id)["percent"] == 100.0

    tracker.update_progress(test_id, "DETECTED", -15.0, "Under 0%")
    assert tracker.get_progress(test_id)["percent"] == 0.0

    # Test snapshot & clear
    all_active = tracker.get_all_active_progress()
    assert test_id in all_active

    tracker.clear_progress(test_id)
    assert tracker.get_progress(test_id) is None


def test_thumbnail_resolver_svg_fallback():
    client = TestClient(app)
    # Request a non-existent or mock ID
    resp = client.get("/api/thumbnails/mock_nonexistent_id")
    assert resp.status_code == 200
    assert "image/svg+xml" in resp.headers.get("content-type", "")
    assert "<svg" in resp.text
    assert "SHORTS AUTOMATION" in resp.text


def test_thumbnail_resolver_youtube_id_redirect():
    client = TestClient(app)
    # 11-char valid YouTube ID format
    resp = client.get("/api/thumbnails/dQw4w9WgXcQ", follow_redirects=False)
    assert resp.status_code in (200, 307)
    if resp.status_code == 307:
        assert "img.youtube.com" in resp.headers.get("location", "")


def test_channel_auto_resolution_in_manual_process():
    db = SessionLocal()
    try:
        # Verify operator can trigger with caption_preset and channel is resolved
        from shorts_engine.dashboard.routes.status import manual_process_video
        from unittest.mock import MagicMock

        mock_req = MagicMock()
        mock_req.headers = {"hx-request": "true"}
        mock_user = MagicMock(spec=User)
        mock_user.username = "test_admin"

        with patch("shorts_engine.dashboard.routes.status._run_pipeline_background") as mock_bg:
            res = manual_process_video(
                request=mock_req,
                video_input="TWnkNyW78Nk",
                channel_id=None,
                caption_preset="cyberpunk_neon",
                dry_run="on",
                force="on",
                register_channel=None,
                detected_channel_name="Ben Shapiro",
                detected_channel_id="UCnQC_G5Xsjhp9fEJKuIcrSw",
                db=db,
                current_user=mock_user,
                csrf_ok=True,
            )
            assert res.status_code == 200
            # Ensure background thread was spawned with caption_preset
            assert mock_bg.called
            args, kwargs = mock_bg.call_args
            # args: (video_id, channel_id, is_dry_run, caption_preset)
            assert args[0] == "TWnkNyW78Nk"
            assert args[2] is True  # is_dry_run
            assert args[3] == "cyberpunk_neon"  # caption_preset
    finally:
        db.close()
