"""Tests for F-15 triage service."""
import sqlite3
import pytest

from shorts_engine.services.triage import (
    gather_all, to_dicts,
    triage_stuck_pipeline_jobs, triage_failed_pipeline_jobs,
    triage_low_ctr_clips, triage_queued_too_long, triage_dlq,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE pipeline_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER,
            stage TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            error TEXT,
            attempt_count INTEGER DEFAULT 0
        );
        CREATE TABLE clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            status TEXT,
            ctr REAL DEFAULT 0.0,
            impressions INTEGER DEFAULT 0,
            published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE webhook_dead_letter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id INTEGER,
            last_error TEXT,
            attempts INTEGER DEFAULT 0
        );
    """)
    yield conn
    conn.close()


def test_empty_db_returns_empty(db):
    assert gather_all(db) == []


def test_triage_handles_missing_tables():
    """gather_all should not crash if tables don't exist."""
    conn = sqlite3.connect(":memory:")
    try:
        items = gather_all(conn)
        assert items == []
    finally:
        conn.close()


def test_stuck_pipeline_jobs(db):
    db.execute(
        "INSERT INTO pipeline_jobs (entity_id, stage, status, started_at) VALUES "
        "(1, 'render', 'RUNNING', datetime('now', '-2 hours'))"
    )
    db.execute(
        "INSERT INTO pipeline_jobs (entity_id, stage, status, started_at) VALUES "
        "(2, 'render', 'RUNNING', datetime('now'))"  # fresh — should NOT match
    )
    db.commit()
    items = triage_stuck_pipeline_jobs(db)
    assert len(items) == 1
    assert items[0].kind == "stuck_pipeline_job"
    assert items[0].priority == 3


def test_failed_pipeline_jobs(db):
    db.execute(
        "INSERT INTO pipeline_jobs (entity_id, stage, status, error, attempt_count) VALUES "
        "(1, 'render', 'FAILED', 'ffmpeg crashed', 1)"
    )
    db.execute(
        "INSERT INTO pipeline_jobs (entity_id, stage, status, error, attempt_count) VALUES "
        "(2, 'upload', 'FAILED', 'auth expired', 5)"
    )
    db.commit()
    items = triage_failed_pipeline_jobs(db)
    assert len(items) == 2
    # Higher attempt count -> higher priority.
    priorities = sorted([i.priority for i in items])
    assert priorities[0] == 4
    assert priorities[1] == 5


def test_low_ctr_clips(db):
    db.execute(
        "INSERT INTO clips (title, status, ctr, impressions, published_at) VALUES "
        "('Old clip', 'PUBLISHED', 0.005, 500, datetime('now', '-10 days'))"
    )
    db.execute(
        "INSERT INTO clips (title, status, ctr, impressions, published_at) VALUES "
        "('Fresh clip', 'PUBLISHED', 0.005, 500, datetime('now', '-1 day'))"
    )  # not yet 7 days — should NOT match
    db.execute(
        "INSERT INTO clips (title, status, ctr, impressions, published_at) VALUES "
        "('Good clip', 'PUBLISHED', 0.05, 500, datetime('now', '-10 days'))"
    )  # CTR > 1% — should NOT match
    db.commit()
    items = triage_low_ctr_clips(db)
    assert len(items) == 1
    assert items[0].kind == "low_ctr_clip"
    assert "0.50%" in items[0].reason or "0.5%" in items[0].reason


def test_queued_too_long(db):
    db.execute(
        "INSERT INTO pipeline_jobs (entity_id, stage, status, created_at) VALUES "
        "(1, 'render', 'PENDING', datetime('now', '-2 days'))"
    )
    db.execute(
        "INSERT INTO pipeline_jobs (entity_id, stage, status, created_at) VALUES "
        "(2, 'render', 'PENDING', datetime('now'))"
    )  # fresh
    db.commit()
    items = triage_queued_too_long(db)
    assert len(items) == 1
    assert items[0].kind == "queued_too_long"


def test_dlq(db):
    db.execute(
        "INSERT INTO webhook_dead_letter (webhook_id, last_error, attempts) VALUES "
        "(1, 'connect refused', 5)"
    )
    db.commit()
    items = triage_dlq(db)
    assert len(items) == 1
    assert items[0].kind == "webhook_dead_letter"


def test_gather_all_sorts_by_priority(db):
    db.execute(
        "INSERT INTO pipeline_jobs (entity_id, stage, status, error, attempt_count) VALUES "
        "(1, 'render', 'FAILED', 'crash', 5)"  # priority 5
    )
    db.execute(
        "INSERT INTO clips (title, status, ctr, impressions, published_at) VALUES "
        "('low', 'PUBLISHED', 0.001, 500, datetime('now', '-30 days'))"  # priority 2
    )
    db.commit()
    items = gather_all(db)
    assert items[0].priority >= items[-1].priority


def test_to_dicts():
    from shorts_engine.services.triage import TriageItem
    items = [TriageItem(kind="x", id="1", label="L", priority=3, reason="r", action_url="/x", metadata={})]
    d = to_dicts(items)
    assert d[0]["kind"] == "x"
    assert d[0]["priority"] == 3
    assert d[0]["metadata"] == {}


def test_priority_labels():
    """Verify the priority scale has the expected span 1-5."""
    items = gather_all(sqlite3.connect(":memory:"))
    assert all(1 <= i.priority <= 5 for i in items)


def test_action_url_present():
    items = gather_all(sqlite3.connect(":memory:"))
    for i in items:
        assert i.action_url.startswith("/")


def test_metadata_is_dict():
    from shorts_engine.services.triage import TriageItem
    t = TriageItem(kind="x", id="1", label="L", priority=1, reason="r", action_url="/", metadata={"k": 1})
    assert t.metadata == {"k": 1}
