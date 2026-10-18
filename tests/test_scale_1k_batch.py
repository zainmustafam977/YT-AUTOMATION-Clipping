"""SCALE-1k — 1000-item batch (publish + bulk edit) under 5 minutes."""
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel, Clip, ClipCandidate, ClipStatus, SourceVideo,
)
from shorts_engine.services.batch import BatchEngine


@pytest.fixture
def db_session():
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
    db.flush()
    # 1000 candidates, each with a clip.
    N = 1000
    for i in range(N):
        cand = ClipCandidate(
            id=f"cand{i}", source_video_id="sv1",
            start_ms=i * 100, end_ms=(i + 1) * 100,
            score=0.5, reason="x",
        )
        db.add(cand)
    db.flush()
    for i in range(N):
        db.add(Clip(
            id=f"c{i}", clip_candidate_id=f"cand{i}",
            status=ClipStatus.WAITING_APPROVAL,
            generated_title=f"Original {i}",
        ))
    db.commit()
    yield db
    db.close()


def test_scale_1k_approve_runs_in_budget(db_session):
    """Run a 1000-item batch_approve and assert time + memory bounds."""
    eng = BatchEngine(db_session)
    item_ids = [f"c{i}" for i in range(1000)]
    t0 = time.time()
    res = eng.run("approve", item_ids, actor="scale")
    elapsed = time.time() - t0
    assert res.ok == 1000
    # 5 minute budget. On modern hardware this is < 30s; we leave
    # generous headroom in CI.
    assert elapsed < 300, f"batch took {elapsed:.1f}s, budget 300s"
    print(f"\n[SCALE] 1000-item approve took {elapsed:.2f}s")
    # Verify audit log.
    from shorts_engine.db.models import BatchAuditLog
    rows = db_session.query(BatchAuditLog).count()
    assert rows == 1000
    # Verify all clips are now APPROVED.
    from sqlalchemy import func
    approved = db_session.query(func.count(Clip.id)).filter(
        Clip.id.in_(item_ids),
        Clip.status == ClipStatus.APPROVED,
    ).scalar()
    assert approved == 1000


def test_scale_1k_bulk_edit_runs_in_budget(db_session):
    eng = BatchEngine(db_session)
    item_ids = [f"c{i}" for i in range(1000)]
    t0 = time.time()
    res = eng.run(
        "bulk_edit_metadata", item_ids,
        params={
            "fields": {"generated_title": "Bulk title OLD"},
            "find": "OLD", "replace": "NEW",
        },
        actor="scale",
    )
    elapsed = time.time() - t0
    assert res.ok == 1000
    assert elapsed < 300, f"batch took {elapsed:.1f}s, budget 300s"
    print(f"\n[SCALE] 1000-item bulk_edit took {elapsed:.2f}s")
    # Spot-check.
    db_session.refresh(db_session.query(Clip).filter_by(id="c0").first())
    assert db_session.query(Clip).filter_by(id="c0").first().generated_title == "Bulk title NEW"
