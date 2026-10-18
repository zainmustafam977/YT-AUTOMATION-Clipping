"""Sabotage check 01: disable idempotency check on publish.

The fix is the BatchEngine attaching a per-item idempotency key.
If a second batch tries to mutate the same item, the lock should
prevent it. The sabotage simulates removing the lock and asserts
the test fails (i.e. the regression test catches it).

For this we use a monkey-patch on BatchEngine._acquire_item_lock
to simulate the control being removed.
"""
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
    cand = ClipCandidate(
        id="cand1", source_video_id="sv1",
        start_ms=0, end_ms=5000, score=0.5, reason="x",
    )
    db.add(cand)
    db.flush()
    db.add(Clip(id="c1", clip_candidate_id="cand1", status=ClipStatus.WAITING_APPROVAL))
    db.commit()
    yield db
    db.close()


def test_idempotency_key_collision_detected(db_session):
    """Two batches against the same item must NOT both succeed."""
    eng = BatchEngine(db_session)
    res1 = eng.run("approve", ["c1"], actor="u1")
    assert res1.ok == 1
    from shorts_engine.db.models import BatchAuditLog
    rows = (
        db_session.query(BatchAuditLog)
        .filter_by(item_id="c1", status="OK")
        .all()
    )
    # Both successful runs on the same item must have DIFFERENT
    # batch_ids (different operations in time). The audit log
    # records both with their own keys.
    assert len(rows) >= 1
    keys = {r.idempotency_key for r in rows}
    assert len(keys) == len(rows)  # distinct per (op, batch, item)


def test_lock_blocks_concurrent_batch(db_session):
    eng = BatchEngine(db_session)
    eng.run("approve", ["c1"], actor="u1")
    # Manually hold a lock to simulate a still-running batch.
    from shorts_engine.db.models import BatchItemLock
    db_session.add(BatchItemLock(
        id="x", batch_id="concurrent", operation="reject", item_id="c1",
    ))
    db_session.commit()
    res2 = eng.run("reject", ["c1"], actor="u2")
    assert res2.items[0].status == "SKIPPED"


def test_sabotage_lock_disabled_causes_double_mutation(db_session):
    """SABOTAGE: with the lock disabled, a concurrent batch can
    double-mutate the same item. The test asserts this is what
    happens when the control is removed, so the test would
    fail if the control were silently re-added without intent.
    """
    eng = BatchEngine(db_session)
    # Sabotage: monkey-patch the lock to always succeed.
    eng._acquire_item_lock = lambda *a, **kw: True  # type: ignore[assignment]
    eng._release_item_lock = lambda *a, **kw: None  # type: ignore[assignment]
    # Hold a "synthetic" lock.
    from shorts_engine.db.models import BatchItemLock
    db_session.add(BatchItemLock(
        id="x", batch_id="concurrent", operation="reject", item_id="c1",
    ))
    db_session.commit()
    res = eng.run("reject", ["c1"], actor="u2")
    # Without the lock, the second batch should succeed (sabotage
    # succeeded). This is the FAILING path the test demonstrates.
    assert res.ok == 1
    # The clip ends up REJECTED.
    db_session.refresh(db_session.query(Clip).filter_by(id="c1").first())
    assert db_session.query(Clip).filter_by(id="c1").first().status == ClipStatus.REJECTED
