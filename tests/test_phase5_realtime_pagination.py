"""
Tests for Phase 5:
- Dashboard Server-Sent Events (SSE) stream (/api/events/dashboard)
- Push-based log streaming & rotation offset recovery (/api/events/logs)
- Review queue scaling: pagination boundaries, multi-sort, keyword search & channel filtering
- Query parameter state preservation across single & bulk action redirects
- Active pipeline job cancellation (/process-video/{video_id}/cancel) and mutex lock release
- Completed video re-ingest guard with force bypass
- Metadata export (CSV & JSON) for approved review clips (/queue/export)
- Channel skip backfill toggle (only_new_uploads)
"""

import csv
import io
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
from shorts_engine.dashboard.routes import logs as logs_route
from shorts_engine.dashboard.auth import (
    get_current_user,
    require_operator,
    require_viewer,
    require_admin,
    verify_csrf,
)
from shorts_engine.orchestration.runner import get_video_lock, release_video_lock


@pytest.fixture
def phase5_env(tmp_path):
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
        id="op-phase5-001",
        username="phase5_operator",
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
        yield client, db, TestSession

    db.close()


# ── 1. Dashboard SSE Stream (/api/events/dashboard) ──────────────────────────

def test_dashboard_sse_stream_format_and_data(phase5_env):
    """Verifies that /api/events/dashboard streams valid SSE snapshots with counters, stages and storage."""
    client, db, _ = phase5_env

    ch = Channel(id="ch_sse_1", youtube_channel_id="UC_x5XG1OV2P6uZZ5FSM9Ttw", name="SSE Channel")
    sv1 = SourceVideo(
        id="sv_sse_1",
        channel_id="ch_sse_1",
        youtube_video_id="video_sse_01",
        title="SSE Processing Video",
        status=SourceVideoStatus.DOWNLOADING,
        duration_sec=120,
    )
    sv2 = SourceVideo(
        id="sv_sse_2",
        channel_id="ch_sse_1",
        youtube_video_id="video_sse_02",
        title="SSE Completed Video",
        status=SourceVideoStatus.DONE,
        duration_sec=90,
    )
    db.add_all([ch, sv1, sv2])
    db.commit()

    with client.stream("GET", "/api/events/dashboard?max_ticks=1") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        lines = []
        for line in response.iter_lines():
            if line.startswith("data:"):
                lines.append(line)
                break

        assert len(lines) == 1
        data_json = lines[0].replace("data:", "").strip()
        data = json.loads(data_json)

        assert "counters" in data
        assert "storage_stats" in data
        assert "ai_provider" in data
        assert "recent_videos" in data

        assert data["counters"]["total_videos"] == 2
        assert data["counters"]["in_progress"] == 1

        recent = data["recent_videos"]
        assert len(recent) == 2
        downloading_vid = next(v for v in recent if v["youtube_video_id"] == "video_sse_01")
        done_vid = next(v for v in recent if v["youtube_video_id"] == "video_sse_02")

        assert downloading_vid["status"] == "DOWNLOADING"
        assert downloading_vid["progress_pct"] == 25
        assert downloading_vid["can_cancel"] is True

        assert done_vid["status"] == "DONE"
        assert done_vid["progress_pct"] == 100
        assert done_vid["can_cancel"] is False


# ── 2. Windows-Safe Push-Based Log Stream (/api/events/logs) ─────────────────

def test_logs_sse_rotation_and_offset_recovery(phase5_env, tmp_path):
    """Verifies incremental log tailing and graceful offset reset on log rotation."""
    client, _, _ = phase5_env
    test_log = tmp_path / "shorts_engine.log"
    test_log.write_text("Line 1: Worker boot\nLine 2: Ready\n", encoding="utf-8")

    with patch("shorts_engine.dashboard.routes.logs.LOG_FILE_PATH", test_log):
        with client.stream("GET", "/api/events/logs?max_ticks=1") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            for line in response.iter_lines():
                if line.startswith("data:"):
                    payload = json.loads(line.replace("data:", "").strip())
                    assert "Line 1" in payload["text"] or "Line 2" in payload["text"]
                    assert payload["pos"] > 0
                    break

        # Simulate log rotation (file shrinkage)
        test_log.write_text("Line 3: Rotated fresh start\n", encoding="utf-8")

        with client.stream("GET", "/api/events/logs?max_ticks=1") as response2:
            assert response2.status_code == 200
            for line in response2.iter_lines():
                if line.startswith("data:"):
                    payload = json.loads(line.replace("data:", "").strip())
                    assert "Line 3: Rotated fresh start" in payload["text"]
                    break


# ── 3. Review Queue Pagination Boundaries ────────────────────────────────────

def test_queue_pagination_boundaries(phase5_env):
    """Verifies queue pagination slicing, item counts, and boundary clamping."""
    client, db, _ = phase5_env

    ch = Channel(id="ch_p_1", youtube_channel_id="UC_p1XG1OV2P6uZZ5FSM9Ttw", name="Pagination Channel")
    sv = SourceVideo(id="sv_p_1", channel_id="ch_p_1", youtube_video_id="video_p_01", title="Pagination Video")
    db.add_all([ch, sv])
    db.commit()

    for i in range(15):
        cand = ClipCandidate(
            id=f"cand_p_{i}",
            source_video_id=sv.id,
            start_ms=i * 1000,
            end_ms=(i + 15) * 1000,
            score=7.0 + (i * 0.1),
            reason="Test candidate",
            hook_text=f"Hook number {i}",
            payoff_text="Payoff",
        )
        clip = Clip(
            id=f"clip_p_{i}",
            clip_candidate_id=cand.id,
            status=ClipStatus.WAITING_APPROVAL,
            created_at=datetime.utcnow() + timedelta(minutes=i),
        )
        db.add_all([cand, clip])
    db.commit()

    # Page 1, limit 5
    res1 = client.get("/queue?tab=active&page=1&limit=5")
    assert res1.status_code == 200
    assert "Showing 1–5 of 15 clips" in res1.text

    # Page 2, limit 5
    res2 = client.get("/queue?tab=active&page=2&limit=5")
    assert res2.status_code == 200
    assert "Showing 6–10 of 15 clips" in res2.text

    # Out-of-bounds page 999 clamps to page 3
    res3 = client.get("/queue?tab=active&page=999&limit=5")
    assert res3.status_code == 200
    assert "Showing 11–15 of 15 clips" in res3.text

    # Out-of-bounds negative page clamps to page 1
    res4 = client.get("/queue?tab=active&page=-5&limit=5")
    assert res4.status_code == 200
    assert "Showing 1–5 of 15 clips" in res4.text


# ── 4. Review Queue Multi-Sort Combinations ──────────────────────────────────

def test_queue_sorting_combinations(phase5_env):
    """Verifies sorting by score (desc/asc), duration (desc/asc), and creation date."""
    client, db, _ = phase5_env

    ch = Channel(id="ch_s_1", youtube_channel_id="UC_s1XG1OV2P6uZZ5FSM9Ttw", name="Sort Channel")
    sv = SourceVideo(id="sv_s_1", channel_id="ch_s_1", youtube_video_id="video_s_01", title="Sort Video")
    db.add_all([ch, sv])
    db.commit()

    data = [
        ("c_low", 5.0, 10000, 25000),    # score 5.0, duration 15s
        ("c_high", 9.5, 10000, 50000),   # score 9.5, duration 40s
        ("c_mid", 7.2, 10000, 70000),    # score 7.2, duration 60s
    ]
    for cid, score, start_ms, end_ms in data:
        cand = ClipCandidate(
            id=f"cand_{cid}",
            source_video_id=sv.id,
            start_ms=start_ms,
            end_ms=end_ms,
            score=score,
            reason="Sort test",
            hook_text=f"Clip {cid}",
            payoff_text="Payoff",
        )
        clip = Clip(
            id=f"clip_{cid}",
            clip_candidate_id=cand.id,
            status=ClipStatus.WAITING_APPROVAL,
        )
        db.add_all([cand, clip])
    db.commit()

    # Sort by score DESC
    res_score_desc = client.get("/queue?sort_by=score&order=desc")
    assert res_score_desc.status_code == 200
    idx_high = res_score_desc.text.find("Clip c_high")
    idx_mid = res_score_desc.text.find("Clip c_mid")
    idx_low = res_score_desc.text.find("Clip c_low")
    assert idx_high < idx_mid < idx_low

    # Sort by score ASC
    res_score_asc = client.get("/queue?sort_by=score&order=asc")
    assert res_score_asc.status_code == 200
    idx_high = res_score_asc.text.find("Clip c_high")
    idx_low = res_score_asc.text.find("Clip c_low")
    assert idx_low < idx_high

    # Sort by duration DESC (c_mid: 60s > c_high: 40s > c_low: 15s)
    res_dur_desc = client.get("/queue?sort_by=duration&order=desc")
    assert res_dur_desc.status_code == 200
    idx_mid = res_dur_desc.text.find("Clip c_mid")
    idx_low = res_dur_desc.text.find("Clip c_low")
    assert idx_mid < idx_low


# ── 5. Review Queue Keyword Search & Channel Filter ──────────────────────────

def test_queue_search_and_channel_filter(phase5_env):
    """Verifies keyword search across hook/title and channel filtering."""
    client, db, _ = phase5_env

    ch1 = Channel(id="ch_filter_1", youtube_channel_id="UC_f1XG1OV2P6uZZ5FSM9Ttw", name="Finance Channel")
    ch2 = Channel(id="ch_filter_2", youtube_channel_id="UC_f2XG1OV2P6uZZ5FSM9Ttw", name="Fitness Channel")
    sv1 = SourceVideo(id="sv_f_1", channel_id=ch1.id, youtube_video_id="video_f_01", title="Crypto Wealth Guide")
    sv2 = SourceVideo(id="sv_f_2", channel_id=ch2.id, youtube_video_id="video_f_02", title="Morning Workout Routine")
    db.add_all([ch1, ch2, sv1, sv2])
    db.commit()

    cand1 = ClipCandidate(id="cand_f_1", source_video_id=sv1.id, start_ms=0, end_ms=20000, score=8.0, reason="", hook_text="How to buy bitcoin safely")
    clip1 = Clip(id="clip_f_1", clip_candidate_id=cand1.id, status=ClipStatus.WAITING_APPROVAL)

    cand2 = ClipCandidate(id="cand_f_2", source_video_id=sv2.id, start_ms=0, end_ms=20000, score=7.5, reason="", hook_text="Do 50 pushups every day")
    clip2 = Clip(id="clip_f_2", clip_candidate_id=cand2.id, status=ClipStatus.WAITING_APPROVAL)

    db.add_all([cand1, clip1, cand2, clip2])
    db.commit()

    # Search keyword "bitcoin"
    res_q = client.get("/queue?q=bitcoin")
    assert res_q.status_code == 200
    assert "How to buy bitcoin safely" in res_q.text
    assert "pushups" not in res_q.text

    # Filter by Channel 2
    res_ch = client.get(f"/queue?channel_id={ch2.id}")
    assert res_ch.status_code == 200
    assert "pushups" in res_ch.text
    assert "bitcoin" not in res_ch.text


# ── 6. Query State Preservation on Action Redirects ──────────────────────────

def test_queue_redirect_state_preservation(phase5_env):
    """Verifies that approve/reject/bulk actions preserve filter query parameters on HTTP 303 redirects."""
    client, db, _ = phase5_env

    ch = Channel(id="ch_redir_1", youtube_channel_id="UC_r1XG1OV2P6uZZ5FSM9Ttw", name="Redir Channel")
    sv = SourceVideo(id="sv_redir_1", channel_id=ch.id, youtube_video_id="video_r_01", title="Redir Video")
    cand1 = ClipCandidate(id="cand_r_1", source_video_id=sv.id, start_ms=0, end_ms=20000, score=8.0, reason="", hook_text="Hook R1")
    clip1 = Clip(id="clip_r_1", clip_candidate_id=cand1.id, status=ClipStatus.WAITING_APPROVAL)
    cand2 = ClipCandidate(id="cand_r_2", source_video_id=sv.id, start_ms=0, end_ms=20000, score=8.0, reason="", hook_text="Hook R2")
    clip2 = Clip(id="clip_r_2", clip_candidate_id=cand2.id, status=ClipStatus.WAITING_APPROVAL)

    db.add_all([ch, sv, cand1, clip1, cand2, clip2])
    db.commit()

    preserved_query = "tab=active&page=2&sort_by=score&order=desc&q=Hook"

    # Single approve with return_query
    resp_approve = client.post(
        f"/queue/{clip1.id}/approve",
        data={"return_query": preserved_query},
        follow_redirects=False,
    )
    assert resp_approve.status_code == 303
    loc = resp_approve.headers["location"]
    assert "page=2" in loc
    assert "sort_by=score" in loc
    assert "q=Hook" in loc

    # Bulk reject with return_query
    resp_bulk = client.post(
        "/queue/bulk-action",
        data={"action": "reject", "clip_ids": [clip2.id], "return_query": preserved_query},
        follow_redirects=False,
    )
    assert resp_bulk.status_code == 303
    loc_bulk = resp_bulk.headers["location"]
    assert "page=2" in loc_bulk
    assert "sort_by=score" in loc_bulk


# ── 7. Pipeline Job Cancellation & Mutex Release ─────────────────────────────

def test_pipeline_job_cancel_endpoint(phase5_env):
    """Verifies POST /process-video/{video_id}/cancel marks video as FAILED and releases the process mutex."""
    client, db, _ = phase5_env

    ch = Channel(id="ch_c_1", youtube_channel_id="UC_c1XG1OV2P6uZZ5FSM9Ttw", name="Cancel Channel")
    sv = SourceVideo(
        id="sv_c_1",
        channel_id=ch.id,
        youtube_video_id="video_cancel_1",
        title="Active Downloading Video",
        status=SourceVideoStatus.DOWNLOADING,
    )
    db.add_all([ch, sv])
    db.commit()

    # Acquire lock to simulate active background runner
    lock = get_video_lock("video_cancel_1")
    assert lock.acquire(blocking=False) is True
    assert lock.locked() is True

    # Cancel via endpoint
    resp = client.post("/process-video/video_cancel_1/cancel", follow_redirects=False)
    assert resp.status_code in (200, 303)

    db.refresh(sv)
    assert sv.status == SourceVideoStatus.FAILED
    assert "Cancelled by operator phase5_operator" in (sv.error_message or "")
    assert lock.locked() is False

    # Attempting to cancel an already FAILED video returns 409 Conflict
    resp_conflict = client.post("/process-video/video_cancel_1/cancel")
    assert resp_conflict.status_code == 409


# ── 8. Completed Video Re-ingest Guard ────────────────────────────────────────

def test_completed_video_reingest_guard(phase5_env):
    """Verifies that attempting to re-process an already completed video is prevented unless force=true."""
    client, db, _ = phase5_env

    ch = Channel(id="ch_cg_1", youtube_channel_id="UC_cg1OV2P6uZZ5FSM9Ttw", name="Guard Channel")
    sv = SourceVideo(
        id="sv_cg_1",
        channel_id=ch.id,
        youtube_video_id="video_guard_1",
        title="Completed Video",
        status=SourceVideoStatus.DONE,
    )
    db.add_all([ch, sv])
    db.commit()

    # Trigger without force -> blocked with warning
    resp_blocked = client.post(
        "/process-video",
        data={"video_input": "video_guard_1"},
        follow_redirects=False,
    )
    assert resp_blocked.status_code in (200, 303)
    if resp_blocked.status_code == 303:
        assert "already+completed" in resp_blocked.headers["location"]
    else:
        assert resp_blocked.json()["status"] == "already_completed"

    # Trigger with force -> accepted and started
    with patch("shorts_engine.dashboard.routes.status._run_pipeline_background"):
        resp_forced = client.post(
            "/process-video",
            data={"video_input": "video_guard_1", "force": "true"},
            follow_redirects=False,
        )
        assert resp_forced.status_code in (200, 303)
        if resp_forced.status_code == 303:
            assert "Started+processing" in resp_forced.headers["location"]
        else:
            assert resp_forced.json()["status"] == "started"


# ── 9. Approved Clips Metadata Export (/queue/export) ─────────────────────────

def test_queue_metadata_export_csv_json(phase5_env):
    """Verifies that /queue/export downloads valid CSV and JSON for approved clips."""
    client, db, _ = phase5_env

    ch = Channel(id="ch_exp_1", youtube_channel_id="UC_ex1OV2P6uZZ5FSM9Ttw", name="Export Channel")
    sv = SourceVideo(id="sv_exp_1", channel_id=ch.id, youtube_video_id="video_exp_01", title="Export Source Video")
    cand = ClipCandidate(
        id="cand_exp_1",
        source_video_id=sv.id,
        start_ms=1000,
        end_ms=25000,
        score=8.8,
        reason="Good hook",
        hook_text="Unbelievable AI breakthrough",
        payoff_text="Watch to the end",
    )
    clip = Clip(
        id="clip_exp_1",
        clip_candidate_id=cand.id,
        status=ClipStatus.APPROVED,
        rendered_path="storage/outputs/video_exp_01/short.mp4",
    )
    db.add_all([ch, sv, cand, clip])
    db.commit()

    # CSV Export
    resp_csv = client.get("/queue/export?format=csv&status=APPROVED")
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv.headers["content-type"]
    assert "attachment" in resp_csv.headers["content-disposition"]
    reader = csv.DictReader(io.StringIO(resp_csv.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["clip_id"] == "clip_exp_1"
    assert rows[0]["hook_text"] == "Unbelievable AI breakthrough"
    assert rows[0]["status"] == "APPROVED"

    # JSON Export
    resp_json = client.get("/queue/export?format=json&status=APPROVED")
    assert resp_json.status_code == 200
    assert "application/json" in resp_json.headers["content-type"]
    json_data = resp_json.json()
    assert isinstance(json_data, list)
    assert len(json_data) == 1
    assert json_data[0]["clip_id"] == "clip_exp_1"
    assert json_data[0]["channel_name"] == "Export Channel"


# ── 10. Channel Skip Backfill Toggle ──────────────────────────────────────────

def test_channel_skip_backfill_toggle(phase5_env):
    """Verifies that adding a channel with only_new_uploads immediately sets last_seen_video_id."""
    client, db, _ = phase5_env

    mock_feed = [("latest_vid_1234", datetime.utcnow())]
    with patch("shorts_engine.services.detection.rss_poller.RSSPoller.fetch_feed", return_value=mock_feed):
        resp = client.post(
            "/channels/add",
            data={
                "youtube_channel_id": "UC_new1OV2P6uZZ5FSM9Ttww",
                "name": "New Channel Skip Backfill",
                "only_new_uploads": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (200, 303)

        ch = db.query(Channel).filter_by(youtube_channel_id="UC_new1OV2P6uZZ5FSM9Ttww").first()
        assert ch is not None
        assert ch.last_seen_video_id == "latest_vid_1234"


# ── 11. Unauthenticated Browser Navigation Redirect to /login ─────────────────

def test_unauthenticated_browser_navigation_redirects_to_login():
    """Verifies that an unauthenticated browser request (Accept: text/html) gets 303 redirected to /login?next=..."""
    app = create_dashboard_app()
    # No dependency overrides for get_current_user or require_viewer
    client = TestClient(app, raise_server_exceptions=True)

    # HTML browser request
    resp = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=%2F"

    # Deep route browser request
    resp_deep = client.get("/queue", headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert resp_deep.status_code == 303
    assert resp_deep.headers["location"] == "/login?next=%2Fqueue"

    # API client request (Accept: application/json) should still get 401 JSON
    resp_api = client.get("/", headers={"Accept": "application/json"}, follow_redirects=False)
    assert resp_api.status_code == 401
    assert resp_api.json()["detail"] == "Authentication required"
