"""
Kill-and-restart persistence tests and SQLite PRAGMA verification.
Verifies:
1. SQLite WAL mode and 30,000ms busy_timeout are active on every connection.
2. Drip-feed schedule (scheduled_publish_at) survives process kill and restart.
3. Operator review decisions (status, quality gate result) survive process kill and restart.
4. PublishLog and AuditLog records survive process kill and restart.
5. Versioned schema migrations table applies and behaves idempotently.
"""

from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker

import pytest

from shorts_engine.db.session import engine as global_engine, Base, init_db
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
    AuditLog,
)
from shorts_engine.db.migrations import run_schema_migrations


def test_sqlite_pragmas_wal_and_busy_timeout():
    """Verifies that SQLite connections enforce WAL mode and 30,000ms busy_timeout."""
    with global_engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()

    assert journal_mode.lower() == "wal", f"Expected WAL mode, got {journal_mode}"
    assert busy_timeout == 30000, f"Expected 30000ms busy_timeout, got {busy_timeout}"
    assert foreign_keys == 1, f"Expected foreign keys enabled (1), got {foreign_keys}"


def _create_isolated_engine(db_path: Path):
    """Creates a standalone SQLAlchemy engine configured with WAL & busy_timeout."""
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30.0},
    )

    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return eng


def test_schedule_state_survives_process_kill_and_restart(tmp_path):
    """
    Simulates writing a scheduled clip, killing the process/disposing engine,
    and restarting with a new engine to assert the schedule survives cleanly.
    """
    db_file = tmp_path / "persistence_schedule_test.db"
    engine_v1 = _create_isolated_engine(db_file)
    init_db(target_engine=engine_v1)

    SessionV1 = sessionmaker(bind=engine_v1)
    target_dt = datetime(2026, 10, 15, 14, 30, 0)
    clip_id = "test-clip-schedule-123"

    with SessionV1() as db:
        channel = Channel(id="chan_sched", name="Sched Channel", youtube_channel_id="UC_sched_1")
        db.add(channel)
        db.flush()

        video = SourceVideo(
            id="vid_sched",
            channel_id=channel.id,
            youtube_video_id="yt_vid_sched",
            title="Video for Scheduling",
            status=SourceVideoStatus.DONE,
        )
        db.add(video)
        db.flush()

        candidate = ClipCandidate(
            id="cand_sched",
            source_video_id=video.id,
            start_ms=1000,
            end_ms=31000,
            score=0.92,
            reason="High engagement hook",
            selected=True,
        )
        db.add(candidate)
        db.flush()

        clip = Clip(
            id=clip_id,
            clip_candidate_id=candidate.id,
            status=ClipStatus.APPROVED,
            scheduled_publish_at=target_dt,
            generated_title="Scheduled Short #shorts",
            rendered_path=str(tmp_path / "rendered.mp4"),
        )
        db.add(clip)
        db.commit()

    # --- SIMULATE PROCESS KILL / SHUTDOWN ---
    engine_v1.dispose()
    del SessionV1
    del engine_v1

    # --- SIMULATE PROCESS RESTART ---
    engine_v2 = _create_isolated_engine(db_file)
    SessionV2 = sessionmaker(bind=engine_v2)

    with SessionV2() as db:
        reloaded_clip = db.query(Clip).filter_by(id=clip_id).first()
        assert reloaded_clip is not None, "Clip did not survive database reload"
        assert reloaded_clip.status == ClipStatus.APPROVED
        assert reloaded_clip.scheduled_publish_at == target_dt
        assert reloaded_clip.generated_title == "Scheduled Short #shorts"

    engine_v2.dispose()


def test_review_decisions_and_publish_logs_survive_restart(tmp_path):
    """
    Verifies that review queue decisions (approved/rejected) and publish logs
    survive a process kill and restart.
    """
    db_file = tmp_path / "persistence_review_test.db"
    engine_v1 = _create_isolated_engine(db_file)
    init_db(target_engine=engine_v1)

    SessionV1 = sessionmaker(bind=engine_v1)
    clip_id = "test-clip-review-456"

    with SessionV1() as db:
        channel = Channel(id="chan_rev", name="Review Channel", youtube_channel_id="UC_rev_1")
        db.add(channel)
        db.flush()

        video = SourceVideo(
            id="vid_rev",
            channel_id=channel.id,
            youtube_video_id="yt_vid_rev",
            title="Review Video",
            status=SourceVideoStatus.DONE,
        )
        db.add(video)
        db.flush()

        candidate = ClipCandidate(
            id="cand_rev",
            source_video_id=video.id,
            start_ms=0,
            end_ms=30000,
            score=0.75,
            reason="Good engagement candidate",
            selected=True,
        )
        db.add(candidate)
        db.flush()

        # Operator marks clip REJECTED with quality check notes
        qc_json = '{"technical_check": {"passed": true}, "operator_note": "Unappealing framing"}'
        clip = Clip(
            id=clip_id,
            clip_candidate_id=candidate.id,
            status=ClipStatus.REJECTED,
            quality_check_result=qc_json,
        )
        db.add(clip)

        # Record publish log for an earlier clip
        pub_log = PublishLog(
            clip_id=clip_id,
            youtube_video_id="yt_published_999",
            published_at=datetime.utcnow(),
            title="Published Short",
            description="Published Short Description",
            status="SUCCESS",
        )
        db.add(pub_log)

        # Record audit log
        audit = AuditLog(
            actor="admin_user",
            event="CLIP_REJECTED",
            details=f"Clip {clip_id} rejected",
        )
        db.add(audit)
        db.commit()

    # --- SIMULATE PROCESS KILL ---
    engine_v1.dispose()
    del SessionV1
    del engine_v1

    # --- SIMULATE PROCESS RESTART ---
    engine_v2 = _create_isolated_engine(db_file)
    SessionV2 = sessionmaker(bind=engine_v2)

    with SessionV2() as db:
        reloaded_clip = db.query(Clip).filter_by(id=clip_id).first()
        assert reloaded_clip is not None
        assert reloaded_clip.status == ClipStatus.REJECTED
        assert "Unappealing framing" in reloaded_clip.quality_check_result

        reloaded_pub = db.query(PublishLog).filter_by(clip_id=clip_id).first()
        assert reloaded_pub is not None
        assert reloaded_pub.youtube_video_id == "yt_published_999"
        assert reloaded_pub.status == "SUCCESS"

        reloaded_audit = db.query(AuditLog).filter_by(actor="admin_user").first()
        assert reloaded_audit is not None
        assert reloaded_audit.event == "CLIP_REJECTED"

    engine_v2.dispose()


def test_schema_migrations_table_is_idempotent(tmp_path):
    """Verifies that versioned schema migrations execute idempotently."""
    db_file = tmp_path / "migrations_test.db"
    eng = _create_isolated_engine(db_file)

    # First run applies all migrations
    new_applied = run_schema_migrations(target_engine=eng)
    assert len(new_applied) > 0

    # Second run applies 0 migrations
    second_applied = run_schema_migrations(target_engine=eng)
    assert len(second_applied) == 0

    eng.dispose()
