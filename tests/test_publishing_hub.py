"""
Unit and Integration Tests for Dedicated Video Publishing Studio Hub (Task 3 & 4).
Verifies:
1. /api/publish/overview returns channels, clips, quota telemetry, and publishing outbox.
2. /api/publish/execute in instant dry-run mode and schedule mode.
3. /api/publish/execute error handling when channel token is missing or invalid.
4. /api/publish/direct-upload ingests external video files into publishing studio.
5. GET /publish HTML Jinja2 template page renders cleanly.
"""

import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.routes.queue import get_db
from shorts_engine.dashboard.auth import require_operator, require_viewer
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, Channel, User, UserRole, PublishLog


def test_publish_overview_endpoint(tmp_path):
    """Verifies /api/publish/overview returns channels with OAuth badges, clips, quota, and history."""
    test_video = tmp_path / "rendered_clip.mp4"
    test_video.write_bytes(b"dummy_video_stream")

    ch = Channel(
        id="c1",
        name="Debateks",
        youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA",
        encrypted_oauth_token=None,
    )
    sv = SourceVideo(id="sv1", youtube_video_id="yt1", title="Source Video 1", channel_id="c1")
    sv.channel = ch
    cand = ClipCandidate(id="cand1", source_video_id="sv1", start_ms=0, end_ms=30000, hook_text="Hook 1", score=8.5)
    cand.source_video = sv

    clip = Clip(
        id="clip-test-overview",
        clip_candidate_id="cand1",
        status=ClipStatus.APPROVED,
        rendered_path=str(test_video),
        generated_title="Top 5 Debate Moments",
    )
    clip.candidate = cand

    log = PublishLog(
        id="log1",
        clip_id="clip-test-overview",
        youtube_video_id="dQw4w9WgXcQ",
        published_at=datetime.utcnow(),
        title="Published Viral Short",
        description="Check it out #Shorts",
        status="SUCCESS",
    )
    log.clip = clip

    mock_db = MagicMock()
    # Handle queries for Channel, Clip, PublishLog
    def mock_query(model):
        q = MagicMock()
        if model == Channel:
            q.order_by.return_value.all.return_value = [ch]
            q.filter_by.return_value.first.return_value = ch
        elif model == Clip:
            q.outerjoin.return_value.outerjoin.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [clip]
            q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
            q.filter_by.return_value.first.return_value = clip
        elif model == PublishLog:
            q.order_by.return_value.limit.return_value.all.return_value = [log]
            q.filter_by.return_value.first.return_value = None
        return q

    mock_db.query.side_effect = mock_query

    fake_user = User(id="u1", username="admin_operator", role=UserRole.ADMIN)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[require_operator] = lambda: fake_user

    client = TestClient(app)

    try:
        res = client.get("/api/publish/overview")
        assert res.status_code == 200
        data = res.json()

        assert "channels" in data
        assert len(data["channels"]) == 1
        assert data["channels"][0]["name"] == "Debateks"
        assert data["channels"][0]["is_authorized"] is False
        assert "Needs Auth" in data["channels"][0]["status_badge"]

        assert "quota" in data
        assert data["quota"]["daily_limit"] == 10000
        assert data["quota"]["cost_per_upload"] == 1600
        assert "remaining_uploads" in data["quota"]

        assert "clips" in data
        assert len(data["clips"]) == 1
        assert data["clips"][0]["id"] == "clip-test-overview"
        assert data["clips"][0]["title"] == "Top 5 Debate Moments"

        assert "history" in data
        assert len(data["history"]) == 1
        assert data["history"][0]["youtube_video_id"] == "dQw4w9WgXcQ"
        assert "youtu.be" in data["history"][0]["watch_url"]

    finally:
        app.dependency_overrides.clear()


def test_publish_execute_schedule_mode(tmp_path):
    """Verifies /api/publish/execute in schedule mode updates scheduled_publish_at."""
    test_video = tmp_path / "clip.mp4"
    test_video.write_bytes(b"content")

    clip = Clip(
        id="clip-sched",
        status=ClipStatus.WAITING_APPROVAL,
        rendered_path=str(test_video),
    )

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = clip

    fake_user = User(id="u1", username="operator1", role=UserRole.OPERATOR)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_operator] = lambda: fake_user

    client = TestClient(app)

    try:
        target_time = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"
        payload = {
            "clip_id": "clip-sched",
            "publish_mode": "schedule",
            "scheduled_at": target_time,
            "title": "Scheduled Release Title",
            "description": "Scheduled description",
            "category_id": "22",
            "privacy_status": "private",
        }

        res = client.post("/api/publish/execute", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "scheduled"
        assert clip.status == ClipStatus.APPROVED
        assert clip.scheduled_publish_at is not None
        assert clip.generated_title == "Scheduled Release Title"

    finally:
        app.dependency_overrides.clear()


def test_publish_execute_instant_dry_run(tmp_path):
    """Verifies /api/publish/execute in instant dry-run mode returns published status."""
    test_video = tmp_path / "clip_dry.mp4"
    test_video.write_bytes(b"video_bytes")

    ch = Channel(id="c1", name="Debateks", youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA")
    sv = SourceVideo(id="sv1", youtube_video_id="yt1", title="SV1", channel_id="c1")
    sv.channel = ch
    cand = ClipCandidate(id="cand1", source_video_id="sv1", start_ms=0, end_ms=15000)
    cand.source_video = sv

    clip = Clip(
        id="clip-dry-123",
        status=ClipStatus.APPROVED,
        rendered_path=str(test_video),
    )
    clip.candidate = cand

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = clip

    fake_user = User(id="u1", username="operator1", role=UserRole.OPERATOR)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_operator] = lambda: fake_user

    client = TestClient(app)

    try:
        payload = {
            "clip_id": "clip-dry-123",
            "publish_mode": "instant",
            "title": "Dry Run Short",
            "description": "Simulation",
            "dry_run": True,
        }

        res = client.post("/api/publish/execute", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "published"
        assert data["is_dry_run"] is True
        assert "dry_run_" in data["youtube_video_id"]

    finally:
        app.dependency_overrides.clear()


def test_publish_execute_live_without_token_returns_clean_error(tmp_path):
    """Verifies /api/publish/execute returns error status when live publishing without OAuth token."""
    test_video = tmp_path / "clip_live.mp4"
    test_video.write_bytes(b"video_bytes")

    ch = Channel(id="c1", name="Debateks", youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA", encrypted_oauth_token=None)
    sv = SourceVideo(id="sv1", youtube_video_id="yt1", title="SV1", channel_id="c1")
    sv.channel = ch
    cand = ClipCandidate(id="cand1", source_video_id="sv1", start_ms=0, end_ms=15000)
    cand.source_video = sv

    clip = Clip(
        id="clip-live-no-token",
        status=ClipStatus.APPROVED,
        rendered_path=str(test_video),
    )
    clip.candidate = cand

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = clip

    fake_user = User(id="u1", username="operator1", role=UserRole.OPERATOR)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_operator] = lambda: fake_user

    client = TestClient(app)

    try:
        payload = {
            "clip_id": "clip-live-no-token",
            "publish_mode": "instant",
            "title": "Live Upload Attempt",
            "dry_run": False,
        }

        res = client.post("/api/publish/execute", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "error"
        assert "OAuth token" in data["message"]

    finally:
        app.dependency_overrides.clear()


def test_publish_direct_upload_endpoint(tmp_path):
    """Verifies /api/publish/direct-upload accepts video files and registers clip."""
    fake_user = User(id="u1", username="operator1", role=UserRole.OPERATOR)
    mock_channel = Channel(id="c1", name="Debateks", youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA")
    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_channel

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_operator] = lambda: fake_user

    client = TestClient(app)

    try:
        # 1. Valid video upload
        file_content = b"fake_mp4_binary_stream"
        files = {"file": ("my_viral_video.mp4", io.BytesIO(file_content), "video/mp4")}
        data = {"title": "External Ingested Short"}

        with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run") as mock_ffprobe:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "25.4\n"
            mock_ffprobe.return_value = mock_res

            res = client.post("/api/publish/direct-upload", files=files, data=data)
            assert res.status_code == 200
            json_res = res.json()
            assert json_res["status"] == "success"
            assert json_res["title"] == "External Ingested Short"
            assert json_res["duration_sec"] == 25.4
            assert Path(json_res["rendered_path"]).exists()

        # 2. Invalid non-video upload rejected
        bad_files = {"file": ("script.sh", io.BytesIO(b"echo 123"), "text/plain")}
        res_bad = client.post("/api/publish/direct-upload", files=bad_files)
        assert res_bad.status_code == 400

    finally:
        app.dependency_overrides.clear()


def test_publish_jinja2_template_renders():
    """Verifies GET /publish renders HTML template cleanly."""
    fake_user = User(id="u1", username="viewer1", role=UserRole.VIEWER)
    ch = Channel(id="c1", name="Debateks", youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA")

    mock_db = MagicMock()
    def mock_query(model):
        q = MagicMock()
        if model == Channel:
            q.order_by.return_value.all.return_value = [ch]
            q.first.return_value = ch
            q.filter_by.return_value.first.return_value = ch
        elif model == Clip:
            q.outerjoin.return_value.outerjoin.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
            q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        elif model == PublishLog:
            q.order_by.return_value.limit.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = mock_query

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_viewer] = lambda: fake_user

    client = TestClient(app)

    try:
        res = client.get("/publish")
        assert res.status_code == 200
        assert "Video Publishing Studio" in res.text
        assert "Debateks" in res.text
        assert "YouTube API Quota" in res.text
    finally:
        app.dependency_overrides.clear()


def test_publish_execute_corrupt_raw_token_resets_clip_status(tmp_path):
    """Verifies that when a channel has a raw secret/corrupt token, publish returns a clean error and clip status is NOT stuck in PUBLISHING."""
    from shorts_engine.db.security import encrypt_secret

    test_video = tmp_path / "corrupt_token_clip.mp4"
    test_video.write_bytes(b"content")

    # Store raw client secret string as token (exactly matching channel Debateks initial state)
    ch = Channel(
        id="c_corrupt",
        name="Debateks",
        youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA",
        encrypted_oauth_token=encrypt_secret("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"),
    )
    sv = SourceVideo(id="sv_c", youtube_video_id="yt_c", title="SV Corrupt", channel_id="c_corrupt")
    sv.channel = ch
    cand = ClipCandidate(id="cand_c", source_video_id="sv_c", start_ms=0, end_ms=10000)
    cand.source_video = sv

    clip = Clip(
        id="clip-corrupt-token",
        status=ClipStatus.APPROVED,
        rendered_path=str(test_video),
    )
    clip.candidate = cand

    mock_db = MagicMock()
    mock_log = PublishLog(id="pl_corrupt", clip_id="clip-corrupt-token")
    def mock_query(model):
        q = MagicMock()
        if model == Clip:
            q.filter_by.return_value.first.return_value = clip
        elif model == PublishLog:
            q.filter_by.return_value.first.return_value = mock_log
        return q
    mock_db.query.side_effect = mock_query

    fake_user = User(id="u1", username="operator1", role=UserRole.OPERATOR)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_operator] = lambda: fake_user

    client = TestClient(app)

    try:
        payload = {
            "clip_id": "clip-corrupt-token",
            "publish_mode": "instant",
            "title": "Live Upload With Corrupt Token",
            "dry_run": False,
        }

        res = client.post("/api/publish/execute", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "error"
        # Verify friendly error message instead of raw JSONDecodeError
        assert "OAuth" in data["message"] or "authorize" in data["message"].lower()
        # Verify clip is NOT stuck in PUBLISHING
        assert clip.status == ClipStatus.APPROVED

    finally:
        app.dependency_overrides.clear()

