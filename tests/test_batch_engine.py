"""Tests for E-001 + R3 batch engine."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    BatchJob,
    Channel,
    Clip,
    ClipCandidate,
    ClipStatus,
    SourceVideo,
)
from shorts_engine.services.batch import BatchEngine, BatchError


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
    cand2 = ClipCandidate(
        id="cand2", source_video_id="sv1",
        start_ms=5000, end_ms=10000, score=0.5, reason="x",
    )
    db.add(cand2)
    db.flush()
    db.add(Clip(id="c1", clip_candidate_id="cand1", status=ClipStatus.WAITING_APPROVAL))
    db.add(Clip(id="c2", clip_candidate_id="cand2", status=ClipStatus.APPROVED))
    db.commit()
    yield db
    db.close()


def test_engine_lists_25_operations(db_session):
    ops = BatchEngine(db_session).list_operations()
    assert len(ops) >= 25
    for op in ["approve", "reject", "publish", "bulk_edit_metadata", "ingest_urls"]:
        assert op in ops


def test_preview_returns_dry_run(db_session):
    res = BatchEngine(db_session).preview("approve", ["c1"], {})
    assert res.batch_id == "preview"
    assert res.items[0].status == "OK"
    assert res.items[0].after.get("dry_run") is True


def test_preview_rejects_unknown_op(db_session):
    with pytest.raises(BatchError, match="unknown"):
        BatchEngine(db_session).preview("nope", ["c1"])


def test_run_approve_changes_status(db_session):
    res = BatchEngine(db_session).run("approve", ["c1"], actor="u1")
    assert res.ok == 1
    db_session.refresh(db_session.query(Clip).filter_by(id="c1").first())
    assert db_session.query(Clip).filter_by(id="c1").first().status == ClipStatus.APPROVED


def test_run_reject_sets_reason_code(db_session):
    res = BatchEngine(db_session).run("reject", ["c1"], params={"reason_code": "LOW_QUALITY"}, actor="u1")
    assert res.ok == 1
    clip = db_session.query(Clip).filter_by(id="c1").first()
    assert clip.status == ClipStatus.REJECTED
    assert clip.rejection_reason_code == "LOW_QUALITY"


def test_run_schedule_persists_when(db_session):
    res = BatchEngine(db_session).run(
        "schedule", ["c1"],
        params={"when": "2026-11-01T10:00:00"},
        actor="u1",
    )
    assert res.ok == 1
    clip = db_session.query(Clip).filter_by(id="c1").first()
    assert clip.scheduled_publish_at is not None


def test_run_bulk_edit_metadata_supports_find_replace(db_session):
    clip = db_session.query(Clip).filter_by(id="c1").first()
    clip.generated_title = "Old title OLD"
    db_session.commit()
    res = BatchEngine(db_session).run(
        "bulk_edit_metadata", ["c1"],
        params={"fields": {"generated_title": "New title OLD"}, "find": "OLD", "replace": "NEW"},
        actor="u1",
    )
    assert res.ok == 1
    db_session.refresh(clip)
    assert clip.generated_title == "New title NEW"


def test_run_ingest_urls_dedupes(db_session):
    res1 = BatchEngine(db_session).run("ingest_urls", ["yt-dup"], params={"channel_id": "ch1"}, actor="u1")
    assert res1.ok == 1
    res2 = BatchEngine(db_session).run("ingest_urls", ["yt-dup"], params={"channel_id": "ch1"}, actor="u1")
    assert res2.items[0].status == "SKIPPED"


def test_run_unknown_clip_returns_failed(db_session):
    res = BatchEngine(db_session).run("approve", ["missing"], actor="u1")
    assert res.items[0].status == "FAILED"


def test_idempotency_key_set_per_item(db_session):
    res = BatchEngine(db_session).run("approve", ["c1"], actor="u1")
    assert res.items[0].idempotency_key


def test_undo_with_items_reverses_approve(db_session):
    BatchEngine(db_session).run("approve", ["c1"], actor="u1")
    batch_id = db_session.query(BatchJob).order_by(BatchJob.created_at.desc()).first().id
    res = BatchEngine(db_session).undo_with_items(batch_id, ["c1"], actor="u1")
    assert res.ok == 1
    db_session.refresh(db_session.query(Clip).filter_by(id="c1").first())
    assert db_session.query(Clip).filter_by(id="c1").first().status == ClipStatus.REJECTED


def test_undo_rejects_irreversible_op(db_session):
    BatchEngine(db_session).run("publish", ["c1"], actor="u1")
    batch_id = db_session.query(BatchJob).order_by(BatchJob.created_at.desc()).first().id
    with pytest.raises(BatchError, match="not reversible"):
        BatchEngine(db_session).undo_with_items(batch_id, ["c1"])


def test_concurrent_batches_serialise_on_same_item(db_session):
    # First batch holds the lock; second batch sees SKIPPED.
    BatchEngine(db_session).run("approve", ["c1"], actor="u1")
    # Manually hold a lock to simulate a still-running batch.
    from shorts_engine.db.models import BatchItemLock
    db_session.add(BatchItemLock(
        id=str(uuid.uuid4()),
        batch_id="concurrent", operation="reject", item_id="c1",
    ))
    db_session.commit()
    res2 = BatchEngine(db_session).run("reject", ["c1"], actor="u2")
    assert res2.items[0].status == "SKIPPED"


def test_audit_log_written_per_item(db_session):
    from shorts_engine.db.models import BatchAuditLog
    BatchEngine(db_session).run("approve", ["c1", "c2"], actor="u1")
    rows = db_session.query(BatchAuditLog).all()
    assert len(rows) == 2
    assert all(r.actor == "u1" for r in rows)
    assert all(r.idempotency_key for r in rows)


def test_publish_quota_aware_jitter(db_session, monkeypatch):
    """Mock the QuotaTracker so we don't depend on real quota state."""
    class FakeQuota:
        def __init__(self):
            self.exhausted = False
            self.consumed = 0

        def is_exhausted(self):
            return self.exhausted

        def consume(self, units):
            self.consumed += units

    fake = FakeQuota()
    monkeypatch.setattr(
        "shorts_engine.services.batch._get_quota_tracker", lambda: fake
    )
    res = BatchEngine(db_session).run("publish", ["c1"], actor="u1")
    assert res.ok == 1
    assert fake.consumed == 50
    # Exhaust quota: next batch returns SKIPPED.
    fake.exhausted = True
    res2 = BatchEngine(db_session).run("publish", ["c1"], actor="u1")
    assert res2.items[0].status == "SKIPPED"


def test_metrics_have_batch_id_key(db_session):
    """Every audit row carries the batch_id; the metric label is keyed on it."""
    from shorts_engine.db.models import BatchAuditLog
    res = BatchEngine(db_session).run("approve", ["c1"], actor="u1")
    rows = db_session.query(BatchAuditLog).filter_by(batch_id=res.batch_id).all()
    assert len(rows) == 1
    assert rows[0].batch_id == res.batch_id
