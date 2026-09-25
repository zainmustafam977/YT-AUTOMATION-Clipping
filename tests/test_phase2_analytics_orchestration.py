"""
Unit & Integration Tests for Phase 2: Analytics & Orchestration.
Tests:
- Visual pipeline graph stage computation (compute_pipeline_stages)
- Auto-scheduling endpoint (/queue/{clip_id}/schedule)
- Clip model scheduled_publish_at and analytics_json properties
- YouTubeAnalyticsSync service and /api/analytics/sync route
"""

from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
    User,
    UserRole,
)
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.routes.status import compute_pipeline_stages
from shorts_engine.services.monitoring.analytics_sync import YouTubeAnalyticsSync
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf


@pytest.fixture
def test_setup():
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

    fake_user = User(username="test_op", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app)
    db = TestSession()

    return {"client": client, "db": db}


def test_compute_pipeline_stages():
    # 1. Downloading
    st = compute_pipeline_stages(SourceVideoStatus.DOWNLOADING.value, 0, 0, 0, 0, 0, False)
    assert st["ingest"] == "active"
    assert st["transcribe"] == "pending"

    # 2. Transcribing
    st = compute_pipeline_stages(SourceVideoStatus.TRANSCRIBING.value, 0, 0, 0, 0, 0, False)
    assert st["ingest"] == "done"
    assert st["transcribe"] == "active"

    # 3. Analyzing / AI Select
    st = compute_pipeline_stages(SourceVideoStatus.ANALYZING.value, 0, 0, 0, 0, 0, False)
    assert st["ingest"] == "done"
    assert st["transcribe"] == "done"
    assert st["select"] == "active"

    # 4. Done
    st = compute_pipeline_stages(SourceVideoStatus.DONE.value, 3, 0, 3, 0, 0, False)
    assert st["ingest"] == "done"
    assert st["transcribe"] == "done"
    assert st["select"] == "done"
    assert st["render"] == "done"
    assert st["gate"] == "done"

    # 5. Skipped Short
    st = compute_pipeline_stages(SourceVideoStatus.DONE.value, 0, 0, 0, 0, 0, True)
    assert st["ingest"] == "done"


def test_schedule_clip_endpoint(test_setup):
    client = test_setup["client"]
    db = test_setup["db"]

    ch = Channel(name="Test Channel", youtube_channel_id="UC_TEST_SCHED")
    db.add(ch)
    db.commit()

    sv = SourceVideo(channel_id=ch.id, youtube_video_id="sched123456", title="Sched Test Video", status=SourceVideoStatus.DONE)
    db.add(sv)
    db.commit()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=30000, score=0.9, reason="Great hook")
    db.add(cand)
    db.commit()

    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.WAITING_APPROVAL)
    db.add(clip)
    db.commit()

    # Post to schedule endpoint
    target_time = "2026-09-25T17:30"
    resp = client.post(
        f"/queue/{clip.id}/schedule",
        data={"scheduled_at": target_time},
        headers={"HX-Request": "true"}
    )
    assert resp.status_code == 200
    assert "clipApproved" in resp.headers.get("HX-Trigger", "")

    # Verify DB state
    db.refresh(clip)
    assert clip.status == ClipStatus.APPROVED
    assert clip.scheduled_publish_at == datetime.fromisoformat(target_time)


def test_youtube_analytics_sync(test_setup):
    db = test_setup["db"]
    client = test_setup["client"]

    ch = Channel(name="Analytics Channel", youtube_channel_id="UC_ANALYTICS")
    db.add(ch)
    db.commit()

    sv = SourceVideo(channel_id=ch.id, youtube_video_id="analy123456", title="Analytics Video", status=SourceVideoStatus.DONE)
    db.add(sv)
    db.commit()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=25000, score=0.85, reason="Viral hook")
    db.add(cand)
    db.commit()

    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.PUBLISHED)
    db.add(clip)
    db.commit()

    pl = PublishLog(
        clip_id=clip.id,
        youtube_video_id="short_yt_analy_123",
        published_at=datetime.utcnow(),
        title="Test Clip Title",
        description="Test Clip Description",
        status="SUCCESS",
    )
    db.add(pl)
    db.commit()

    # Run analytics sync service
    syncer = YouTubeAnalyticsSync(db)
    res = syncer.sync_all_published_clips()

    assert res["synced_count"] == 1
    assert res["total_views"] > 0
    assert res["total_likes"] > 0

    db.refresh(clip)
    assert isinstance(clip.analytics, dict)
    assert clip.analytics.get("views") is not None
    assert clip.analytics.get("retention_pct") is not None

    # Test /api/analytics/sync endpoint
    resp = client.post("/api/analytics/sync", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
