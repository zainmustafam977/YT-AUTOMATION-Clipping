"""
Unit and Integration Tests for Content Calendar Routes:
- Tests /calendar view rendering
- Tests /api/calendar/events JSON feed (scheduled vs published clips)
- Tests /api/calendar/reschedule/{clip_id} drag-and-drop endpoint
"""

from datetime import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from shorts_engine.db.session import Base
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel, PublishLog, User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import calendar as calendar_route
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
    app.dependency_overrides[calendar_route.get_db] = override_get_db

    fake_user = User(username="calendar_op", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app)
    db = TestSession()

    return {"client": client, "db": db}


def test_calendar_html_view(test_setup):
    client = test_setup["client"]
    resp = client.get("/calendar")
    assert resp.status_code == 200
    assert "Content Publication Calendar" in resp.text
    assert "FullCalendar" in resp.text


def test_calendar_events_feed(test_setup):
    client = test_setup["client"]
    db = test_setup["db"]

    ch = Channel(id="ch_cal_1", youtube_channel_id="UC_cal_1", name="Calendar Channel")
    db.add(ch)
    sv = SourceVideo(id="sv_cal_1", youtube_video_id="yt_cal_1", channel_id=ch.id, title="Test Source", status=SourceVideoStatus.DONE)
    db.add(sv)
    cand_sched = ClipCandidate(id="cand_cal_sched", source_video_id=sv.id, start_ms=0, end_ms=15000, hook_text="Calendar Hook", payoff_text="End", score=0.88, reason="Punchy")
    cand_pub = ClipCandidate(id="cand_cal_pub", source_video_id=sv.id, start_ms=15000, end_ms=30000, hook_text="Published Hook", payoff_text="End", score=0.92, reason="Punchy")
    db.add_all([cand_sched, cand_pub])

    # 1. Scheduled clip
    clip_sched = Clip(
        id="clip_sched_1",
        clip_candidate_id=cand_sched.id,
        status=ClipStatus.APPROVED,
        scheduled_publish_at=datetime(2026, 9, 21, 15, 0, 0),
        generated_title="Scheduled Title",
    )
    db.add(clip_sched)

    # 2. Published clip
    clip_pub = Clip(
        id="clip_pub_1",
        clip_candidate_id=cand_pub.id,
        status=ClipStatus.PUBLISHED,
        generated_title="Published Title",
    )
    db.add(clip_pub)


    pub_log = PublishLog(
        clip_id=clip_pub.id,
        youtube_video_id="yt_short_123",
        published_at=datetime(2026, 9, 20, 12, 0, 0),
        title="Published Title #shorts",
        description="Desc",
        status="SUCCESS",
    )
    db.add(pub_log)
    db.commit()

    resp = client.get("/api/calendar/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    # Check scheduled event
    sched_event = next(e for e in data if e["id"] == "clip_sched_1")
    assert "Scheduled Title" in sched_event["title"]
    assert sched_event["extendedProps"]["is_published"] is False
    assert sched_event["backgroundColor"] == "#0dcaf0"

    # Check published event
    pub_event = next(e for e in data if e["id"] == f"pub_{pub_log.id}")
    assert "Published Title" in pub_event["title"]
    assert pub_event["extendedProps"]["is_published"] is True
    assert pub_event["backgroundColor"] == "#198754"


def test_calendar_reschedule_endpoint(test_setup):
    client = test_setup["client"]
    db = test_setup["db"]

    cand = ClipCandidate(id="cand_cal_2", source_video_id="sv_cal_1", start_ms=0, end_ms=10000, hook_text="Reschedule", payoff_text="Done", score=0.9, reason="Good")
    db.add(cand)
    clip = Clip(
        id="clip_resched_1",
        clip_candidate_id=cand.id,
        status=ClipStatus.WAITING_APPROVAL,
        scheduled_publish_at=datetime(2026, 9, 21, 10, 0, 0),
    )
    db.add(clip)
    db.commit()

    # 1. Attempting to reschedule WAITING_APPROVAL clip must be rejected with 400
    resp_unapproved = client.post(
        f"/api/calendar/reschedule/{clip.id}",
        data={"scheduled_at": "2026-09-22T18:30:00Z"},
    )
    assert resp_unapproved.status_code == 400
    assert "awaiting approval" in resp_unapproved.json()["detail"].lower()
    db.refresh(clip)
    assert clip.status == ClipStatus.WAITING_APPROVAL

    # 2. Once approved, rescheduling succeeds and updates timestamp
    clip.status = ClipStatus.APPROVED
    db.commit()

    resp = client.post(
        f"/api/calendar/reschedule/{clip.id}",
        data={"scheduled_at": "2026-09-22T18:30:00Z"},
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "rescheduled"
    assert "2026-09-22T18:30:00" in res["scheduled_publish_at"]

    db.refresh(clip)
    assert clip.scheduled_publish_at == datetime(2026, 9, 22, 18, 30, 0)
    assert clip.status == ClipStatus.APPROVED
