"""
Tests for Recent Ingestion & Pipeline Activity Enhancements:
- Batched query performance and candidate clip summaries (2-query batching)
- Origin detection (Watched Channel vs Manual Trigger)
- 1-Click retry endpoint for failed ingestions (/process-video/{video_id}/retry)
- Guard against retrying non-failed or locked videos (409 Conflict)
- Cooperative cancellation abort checks in PipelineRunner
- Skipped short detection and turnaround formatting
- Real-time SSE dashboard event stream containing enriched recent videos
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    User,
    UserRole,
)
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.routes import channels as channels_route
from shorts_engine.dashboard.auth import (
    get_current_user,
    require_operator,
    require_viewer,
    require_admin,
    verify_csrf,
)
from shorts_engine.orchestration.runner import (
    PipelineRunner,
    get_video_lock,
    release_video_lock,
    register_cancellation,
    is_video_cancelled,
    clear_cancellation,
)


@pytest.fixture
def activity_env(tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_dashboard_app()
    app.dependency_overrides[status_route.get_db] = override_get_db
    app.dependency_overrides[queue_route.get_db] = override_get_db
    app.dependency_overrides[channels_route.get_db] = override_get_db

    test_operator = User(
        id="op-recent-001",
        username="test_operator",
        password_hash="hash",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: test_operator
    app.dependency_overrides[require_viewer] = lambda: test_operator
    app.dependency_overrides[require_operator] = lambda: test_operator
    app.dependency_overrides[require_admin] = lambda: test_operator
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app, raise_server_exceptions=True)
    db = TestSession()

    with patch("shorts_engine.dashboard.routes.status.SessionLocal", TestSession):
        yield client, db, engine, TestSession

    db.close()


# ── 1. Batched Query Performance & Candidate Summary ─────────────────────────

def test_batched_query_performance_and_candidates_summary(activity_env):
    """
    Verifies build_recent_videos_payload executes exactly 2 queries
    regardless of video count, and generates rich candidate metadata.
    """
    client, db, engine, _ = activity_env

    # Create channels: 1 watched, 1 manual
    ch_watched = Channel(id="ch_w_1", youtube_channel_id="UC_watched1OV2P6uZZ5FSM9", name="Tech Explains", watch_enabled=True)
    ch_manual = Channel(id="ch_m_1", youtube_channel_id="UC_manual1OV2P6uZZ5FSM9T", name="Ad-hoc Channel", watch_enabled=False)
    db.add_all([ch_watched, ch_manual])
    db.commit()

    # Create 5 videos with candidates
    now = datetime.utcnow()
    videos = []
    for i in range(5):
        vid = f"vid_test_payload_{i}"
        sv = SourceVideo(
            id=f"sv_{vid}",
            channel_id=ch_watched.id if i % 2 == 0 else ch_manual.id,
            youtube_video_id=vid,
            title=f"Test Video {i}",
            status=SourceVideoStatus.DONE if i % 2 == 0 else SourceVideoStatus.DOWNLOADING,
            duration_sec=300 + i * 60,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=5),
        )
        videos.append(sv)
    db.add_all(videos)
    db.commit()

    # Add candidates to each video
    candidates = []
    for sv in videos:
        cand = ClipCandidate(
            id=f"cand_{sv.youtube_video_id}",
            source_video_id=sv.id,
            start_ms=10000,
            end_ms=45000,
            score=8.7,
            reason="High engagement hook",
            hook_text="Did you know this secret?",
            payoff_text="That is why it works.",
        )
        candidates.append(cand)
    db.add_all(candidates)
    db.commit()

    # Add a Clip record for one candidate
    clip = Clip(
        id="clip_c_1",
        clip_candidate_id=candidates[0].id,
        status=ClipStatus.WAITING_APPROVAL,
    )
    db.add(clip)
    db.commit()

    # Measure exact SQL statement count during execution
    query_count = 0
    def count_queries(conn, cursor, statement, parameters, context, executemany):
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        payload = status_route.build_recent_videos_payload(db, limit=25)
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)

    # Must execute exactly 2 queries: Query 1 for SourceVideos, Query 2 for ClipCandidates
    assert query_count == 2, f"Expected 2 queries, got {query_count}"
    assert len(payload) == 5

    # Validate first video structure
    v0 = next(v for v in payload if v["youtube_video_id"] == "vid_test_payload_0")
    assert v0["thumbnail_url"] == "/api/thumbnails/vid_test_payload_0"
    assert v0["origin"] == "Watched Channel"
    assert v0["channel_name"] == "Tech Explains"
    assert v0["total_clips"] == 1
    assert v0["waiting_clips"] == 1
    assert "Finished in" in v0["turnaround_text"]
    assert len(v0["candidates"]) == 1
    c0 = v0["candidates"][0]
    assert c0["score"] == 8.7
    assert c0["hook_text"] == "Did you know this secret?"
    assert c0["status"] == "WAITING_APPROVAL"
    assert c0["duration_sec"] == 35.0

    # Validate manual trigger origin
    v1 = next(v for v in payload if v["youtube_video_id"] == "vid_test_payload_1")
    assert v1["origin"] == "Manual Trigger"
    assert "running" in v1["turnaround_text"]
    assert v1["can_cancel"] is True
    assert v1["can_retry"] is False


# ── 2. One-Click Retry Endpoint for Failed Ingestions ────────────────────────

def test_failed_video_one_click_retry_endpoint(activity_env):
    """
    Verifies POST /process-video/{video_id}/retry resets a FAILED video
    to DETECTED, clears errors, increments retry_count, and launches background processing.
    """
    client, db, _, _ = activity_env

    ch = Channel(id="ch_retry_1", youtube_channel_id="UC_retry1OV2P6uZZ5FSM9Tt", name="Retry Channel")
    sv = SourceVideo(
        id="sv_retry_1",
        channel_id=ch.id,
        youtube_video_id="video_retry_01",
        title="Failed Pipeline Video",
        status=SourceVideoStatus.FAILED,
        error_message="Whisper CUDA Out Of Memory error",
        retry_count=1,
    )
    db.add_all([ch, sv])
    db.commit()

    # Pre-register cancellation flag to verify it gets cleared
    register_cancellation("video_retry_01")
    assert is_video_cancelled("video_retry_01") is True

    with patch("shorts_engine.dashboard.routes.status._run_pipeline_background") as mock_bg:
        resp = client.post(
            "/process-video/video_retry_01/retry",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "retrying"
        assert data["video_id"] == "video_retry_01"
        assert data["retry_count"] == 2

        # Verify background thread was spawned with target
        mock_bg.assert_called_once_with("video_retry_01", ch.id, False)

    db.refresh(sv)
    assert sv.status == SourceVideoStatus.DETECTED
    assert sv.error_message is None
    assert sv.retry_count == 2
    # Cancellation flag must be cleared
    assert is_video_cancelled("video_retry_01") is False


def test_retry_rejects_non_failed_video(activity_env):
    """Verifies that attempting to retry an active or completed video returns 409 Conflict."""
    client, db, _, _ = activity_env

    ch = Channel(id="ch_rf_1", youtube_channel_id="UC_rf1OV2P6uZZ5FSM9Ttw", name="Reject Channel")
    sv_done = SourceVideo(
        id="sv_done_1",
        channel_id=ch.id,
        youtube_video_id="video_done_01",
        title="Done Video",
        status=SourceVideoStatus.DONE,
    )
    sv_running = SourceVideo(
        id="sv_run_1",
        channel_id=ch.id,
        youtube_video_id="video_run_01",
        title="Running Video",
        status=SourceVideoStatus.DOWNLOADING,
    )
    db.add_all([ch, sv_done, sv_running])
    db.commit()

    # Retrying a DONE video should return 409
    resp = client.post("/process-video/video_done_01/retry")
    assert resp.status_code == 409
    assert "Only FAILED jobs can be retried" in resp.json()["detail"]

    # Retrying a DOWNLOADING video should return 409
    resp2 = client.post("/process-video/video_run_01/retry")
    assert resp2.status_code == 409
    assert "Only FAILED jobs can be retried" in resp2.json()["detail"]

    # Retrying a non-existent video returns 404
    resp3 = client.post("/process-video/non_existent_id/retry")
    assert resp3.status_code == 404


def test_retry_rejects_when_mutex_locked(activity_env):
    """Verifies that if a process mutex lock is currently held, retry returns 409 Conflict."""
    client, db, _, _ = activity_env

    ch = Channel(id="ch_rl_1", youtube_channel_id="UC_rl1OV2P6uZZ5FSM9Ttw", name="Lock Channel")
    sv = SourceVideo(
        id="sv_rl_1",
        channel_id=ch.id,
        youtube_video_id="video_locked_01",
        title="Locked Video",
        status=SourceVideoStatus.FAILED,
    )
    db.add_all([ch, sv])
    db.commit()

    v_lock = get_video_lock("video_locked_01")
    assert v_lock.acquire(blocking=False) is True
    try:
        resp = client.post("/process-video/video_locked_01/retry")
        assert resp.status_code == 409
        assert "currently locked" in resp.json()["detail"]
    finally:
        v_lock.release()


# ── 3. Cooperative Cancellation Abort in PipelineRunner ──────────────────────

def test_runner_cooperative_cancellation_abort(activity_env):
    """
    Verifies that when a video is cancelled via register_cancellation / release_video_lock,
    the PipelineRunner stops execution cleanly at step checkpoints without crashing.
    """
    _, db, _, _ = activity_env

    ch = Channel(id="ch_abort_1", youtube_channel_id="UC_abort1OV2P6uZZ5FSM9Tt", name="Abort Channel")
    db.add(ch)
    db.commit()

    runner = PipelineRunner(db)

    # Register cancellation before running
    register_cancellation("video_to_abort")
    assert is_video_cancelled("video_to_abort") is True

    # Run pipeline with a mocked IngestionService that should NOT be called
    with patch.object(runner.ingestion, "download_video") as mock_dl:
        res = runner.run_pipeline_for_video("video_to_abort", channel=ch, dry_run=True)
        assert res["status"] == "CANCELLED"
        assert "Pipeline cancelled by operator" in res["message"]
        mock_dl.assert_not_called()

    # Clean up cancellation flag
    clear_cancellation("video_to_abort")
    assert is_video_cancelled("video_to_abort") is False


# ── 4. Skipped Short Badge & Metadata Formatting ─────────────────────────────

def test_skipped_short_badge_and_metadata(activity_env):
    """
    Verifies that a video pre-filtered as an existing YouTube Short
    is properly recognized with is_skipped_short=True and turnaround_text="Skipped (Already Short)".
    """
    _, db, _, _ = activity_env

    ch = Channel(id="ch_short_1", youtube_channel_id="UC_short1OV2P6uZZ5FSM9Tt", name="Short Channel")
    sv = SourceVideo(
        id="sv_short_1",
        channel_id=ch.id,
        youtube_video_id="video_short_01",
        title="Quick 15s Clip #shorts",
        status=SourceVideoStatus.DONE,
        duration_sec=15,
        error_message="Skipped: already a YouTube Short (duration 15s <= 60s)",
    )
    db.add_all([ch, sv])
    db.commit()

    payload = status_route.build_recent_videos_payload(db, limit=10)
    assert len(payload) == 1
    v = payload[0]
    assert v["is_skipped_short"] is True
    assert v["turnaround_text"] == "Skipped (Already Short)"
    assert v["duration_formatted"] == "15s"


# ── 5. Dashboard SSE Stream Includes Enriched Recent Videos ──────────────────

def test_dashboard_sse_stream_includes_enriched_recent_videos(activity_env):
    """
    Verifies that /api/events/dashboard includes recent_videos in the streamed JSON snapshot
    with thumbnail URLs, candidate summaries, and turnaround telemetry.
    """
    client, db, _, _ = activity_env

    ch = Channel(id="ch_sse_rec", youtube_channel_id="UC_sserec1OV2P6uZZ5FSM9T", name="Live Channel")
    sv = SourceVideo(
        id="sv_sse_rec_1",
        channel_id=ch.id,
        youtube_video_id="sse_vid_01",
        title="Live Streamed Video",
        status=SourceVideoStatus.TRANSCRIBING,
        duration_sec=420,
    )
    db.add_all([ch, sv])
    db.commit()

    resp = client.get("/api/events/dashboard?max_ticks=1")
    assert resp.status_code == 200

    lines = resp.text.split("\n")
    data_lines = [l for l in lines if l.startswith("data: ")]
    assert len(data_lines) >= 1

    payload = json.loads(data_lines[0].replace("data: ", ""))
    assert "recent_videos" in payload
    assert len(payload["recent_videos"]) >= 1

    rec = payload["recent_videos"][0]
    assert rec["youtube_video_id"] == "sse_vid_01"
    assert rec["thumbnail_url"] == "/api/thumbnails/sse_vid_01"
    assert rec["stage_label"] == "Transcribing with Whisper"
    assert rec["progress_pct"] == 50
    assert rec["can_cancel"] is True
    assert rec["can_retry"] is False
    assert "running" in rec["turnaround_text"]
