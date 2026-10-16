"""Sabotage check 10: break batch undo.

The batch undo() and undo_with_items() methods require a registered
UNDO_REGISTRY entry. The sabotage removes a reversible op from the
registry and asserts the undo raises.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel, Clip, ClipCandidate, ClipStatus, SourceVideo,
)
from shorts_engine.services.batch import BatchEngine, UNDO_REGISTRY, BatchError


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


def test_approve_is_reversible(db_session):
    assert "approve" in UNDO_REGISTRY
    eng = BatchEngine(db_session)
    eng.run("approve", ["c1"], actor="u1")
    from shorts_engine.db.models import BatchJob
    batch_id = db_session.query(BatchJob).order_by(BatchJob.created_at.desc()).first().id
    res = eng.undo_with_items(batch_id, ["c1"], actor="u1")
    assert res.ok == 1


def test_undo_rejects_irreversible_op(db_session):
    eng = BatchEngine(db_session)
    eng.run("publish", ["c1"], actor="u1")
    from shorts_engine.db.models import BatchJob
    batch_id = db_session.query(BatchJob).order_by(BatchJob.created_at.desc()).first().id
    with pytest.raises(BatchError, match="not reversible"):
        eng.undo_with_items(batch_id, ["c1"])


def test_sabotage_undo_registry_wiped(db_session, monkeypatch):
    """SABOTAGE: if UNDO_REGISTRY is wiped, undo raises."""
    eng = BatchEngine(db_session)
    eng.run("approve", ["c1"], actor="u1")
    from shorts_engine.db.models import BatchJob
    batch_id = db_session.query(BatchJob).order_by(BatchJob.created_at.desc()).first().id
    monkeypatch.setattr("shorts_engine.services.batch.UNDO_REGISTRY", {})
    with pytest.raises(BatchError, match="not reversible"):
        eng.undo_with_items(batch_id, ["c1"])


def test_undo_unknown_batch(db_session):
    with pytest.raises(BatchError, match="batch not found"):
        BatchEngine(db_session).undo("nonexistent")
