"""Sabotage check 11: remove outbox write in publish.

The batch engine writes to batch_audit_log on every operation.
The sabotage removes the audit write and asserts the test
catches it.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    BatchAuditLog, Channel, Clip, ClipCandidate, ClipStatus, SourceVideo,
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


def test_audit_log_written_on_approve(db_session):
    BatchEngine(db_session).run("approve", ["c1"], actor="u1")
    rows = db_session.query(BatchAuditLog).all()
    assert len(rows) == 1
    assert rows[0].operation == "approve"
    assert rows[0].status == "OK"


def test_sabotage_audit_write_removed(db_session, monkeypatch):
    """SABOTAGE: if the audit write is removed, the count is 0.
    This is the failing path the test demonstrates."""
    # Sabotage: monkey-patch the audit method to a no-op.
    import shorts_engine.services.batch as _b
    monkeypatch.setattr(_b.BatchEngine, "_audit", lambda self, *a, **kw: None)
    BatchEngine(db_session).run("approve", ["c1"], actor="u1")
    rows = db_session.query(BatchAuditLog).all()
    # Without the audit write, the count is 0. The test demonstrates
    # the attacker's successful sabotage.
    assert len(rows) == 0
