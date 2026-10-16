"""Sabotage check 02: remove RBAC on batch approve.

The batch dashboard route requires the operator role. The sabotage
simulates calling the operation without auth and asserts the route
rejects. The actual control is verified by the auth dependency
existing.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel, Clip, ClipCandidate, ClipStatus, SourceVideo,
)
from shorts_engine.dashboard.app import create_dashboard_app


def test_batch_run_without_auth_is_rejected():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    ch = Channel(id="ch1", youtube_channel_id="UC1", name="C")
    db.add(ch)
    sv = SourceVideo(
        id="sv1", channel_id="ch1", youtube_video_id="yt1",
        title="t", duration_sec=60,
    )
    db.add(sv)
    cand = ClipCandidate(
        id="cand1", source_video_id="sv1",
        start_ms=0, end_ms=5000, score=0.5, reason="x",
    )
    db.add(cand)
    db.flush()
    db.add(Clip(id="c1", clip_candidate_id="cand1", status=ClipStatus.WAITING_APPROVAL))
    db.commit()
    db.close()
    app = create_dashboard_app()
    c = TestClient(app)
    # No cookie set.
    resp = c.post(
        "/api/batch/run",
        json={"operation": "approve", "item_ids": ["c1"]},
    )
    assert resp.status_code == 401
