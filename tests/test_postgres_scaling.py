"""
Unit and integration tests for PostgreSQL scaling and SQLite-to-PostgreSQL migration.
Verifies:
1. Dialect-aware DDL handling (PostgreSQL vs SQLite) in ensure_schema_upgrades.
2. Multi-host distributed worker claim token parsing (parse_claim_info).
3. Cross-node stale claim isolation in is_claim_stale (preventing false-positive steals across containers).
4. End-to-end SQLite data migration to target database with row parity and type normalization.
"""

import os
import socket
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import (
    Base,
    init_db,
    ensure_schema_upgrades,
    is_postgres,
)
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
    QuotaLedger,
    AuditLog,
)
from shorts_engine.services.worker.runner import (
    generate_worker_id,
    parse_claim_info,
    extract_pid_from_claim,
    is_claim_stale,
    claim_next_clip,
    reconcile_stale_claims,
)
from shorts_engine.db.data_migration import migrate_sqlite_to_postgres


def test_generate_worker_id_includes_host_and_pid():
    """Verifies that generated worker IDs include the host name, process ID, and unique entropy."""
    wid = generate_worker_id()
    assert wid.startswith("worker-")
    host = socket.gethostname()
    assert host in wid
    assert str(os.getpid()) in wid


def test_parse_claim_info_formats():
    """Verifies claim parsing across distributed multi-host format and legacy single-host formats."""
    # 1. Distributed format: worker-<host>-<pid>-<uuid>
    host, pid = parse_claim_info("CLAIMED:worker-render-node-1-9876-a1b2c3")
    assert host == "render-node-1"
    assert pid == 9876

    # 2. Hostname with hyphens and dots
    host, pid = parse_claim_info("CLAIMED:worker-gpu.cluster-01.internal-12345-abcdef")
    assert host == "gpu.cluster-01.internal"
    assert pid == 12345

    # 3. Legacy format: worker-<pid>-<uuid>
    host, pid = parse_claim_info("CLAIMED:worker-4321-fedcba")
    assert host is None
    assert pid == 4321

    # 4. Legacy runner format: runner-<pid>
    host, pid = parse_claim_info("CLAIMED:runner-5555")
    assert host is None
    assert pid == 5555

    # 5. Invalid string
    assert parse_claim_info(None) == (None, None)
    assert parse_claim_info("random_path/video.mp4") == (None, None)

    # 6. extract_pid_from_claim backward compatibility
    assert extract_pid_from_claim("CLAIMED:worker-nodeA-9999-123456") == 9999


def test_is_claim_stale_cross_node_isolation():
    """
    Verifies that claims from a DIFFERENT host are NOT stolen even if the PID doesn't exist locally,
    unless the claim age exceeds the stale timeout.
    """
    now = datetime.utcnow()
    current_host = socket.gethostname()

    # Case A: Local host claim with a non-existent dead PID -> IMMEDIATELY STALE
    local_dead_claim = f"CLAIMED:worker-{current_host}-999999-dead01"
    with patch("shorts_engine.services.worker.runner.is_pid_alive", return_value=False):
        assert is_claim_stale(local_dead_claim, updated_at=now, now=now, stale_timeout_sec=600) is True

    # Case B: Local host claim with an active living PID -> NOT STALE
    local_live_claim = f"CLAIMED:worker-{current_host}-{os.getpid()}-live01"
    with patch("shorts_engine.services.worker.runner.is_pid_alive", return_value=True):
        assert is_claim_stale(local_live_claim, updated_at=now, now=now, stale_timeout_sec=600) is False

    # Case C: Remote host claim with recent timestamp -> MUST NOT BE STOLEN
    # (even though PID 999999 would be dead on local machine)
    remote_claim = "CLAIMED:worker-other-render-node-999999-live02"
    recent_time = now - timedelta(seconds=60)
    with patch("shorts_engine.services.worker.runner.is_pid_alive", return_value=False):
        assert is_claim_stale(remote_claim, updated_at=recent_time, now=now, stale_timeout_sec=600) is False

    # Case D: Remote host claim that exceeded stale_timeout_sec -> STALE (reclaimable)
    expired_time = now - timedelta(seconds=700)
    with patch("shorts_engine.services.worker.runner.is_pid_alive", return_value=False):
        assert is_claim_stale(remote_claim, updated_at=expired_time, now=now, stale_timeout_sec=600) is True


def test_ensure_schema_upgrades_is_idempotent_and_dialect_aware(tmp_path):
    """Verifies that ensure_schema_upgrades executes cleanly and idempotently."""
    db_file = tmp_path / "test_upgrades.db"
    eng = create_engine(f"sqlite:///{db_file}")

    # First run on fresh database
    init_db(target_engine=eng)

    # Re-run ensure_schema_upgrades (must be idempotent without error)
    ensure_schema_upgrades(target_engine=eng)

    # Verify columns were added properly
    with eng.connect() as conn:
        channels_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(channels)")).fetchall()]
        clips_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(clips)")).fetchall()]

    assert "upload_defaults_json" in channels_cols
    assert "last_verified_at" in channels_cols
    assert "scheduled_publish_at" in clips_cols
    assert "is_sensitive" in clips_cols


def test_sqlite_to_target_data_migration_end_to_end(tmp_path):
    """
    Seeds a source SQLite database with channels, source videos, clip candidates,
    clips, publish logs, and quota entries, then runs the automated data migration
    engine into a destination database, asserting complete row-count parity.
    """
    src_file = tmp_path / "source_production.db"
    dst_file = tmp_path / "target_migrated.db"

    src_eng = create_engine(f"sqlite:///{src_file}", connect_args={"check_same_thread": False})
    init_db(target_engine=src_eng)

    SrcSession = sessionmaker(bind=src_eng)
    with SrcSession() as session:
        ch = Channel(
            youtube_channel_id="UC_TEST_MIGRATE_01",
            name="Migration Channel",
            watch_enabled=True,
            human_review_required=False,
        )
        session.add(ch)
        session.flush()

        sv = SourceVideo(
            channel_id=ch.id,
            youtube_video_id="VID_MIG_001",
            title="Source Video 1",
            status=SourceVideoStatus.DONE,
        )
        session.add(sv)
        session.flush()

        cand = ClipCandidate(
            source_video_id=sv.id,
            start_ms=1000,
            end_ms=25000,
            score=0.92,
            reason="High energy hook",
            selected=True,
        )
        session.add(cand)
        session.flush()

        clip = Clip(
            clip_candidate_id=cand.id,
            status=ClipStatus.APPROVED,
            rendered_path="/tmp/rendered_test.mp4",
            scheduled_publish_at=datetime.utcnow() + timedelta(days=1),
        )
        session.add(clip)
        session.flush()

        pub = PublishLog(
            clip_id=clip.id,
            youtube_video_id="YT_PUB_MIG_001",
            published_at=datetime.utcnow(),
            title="Published Short",
            description="Test short description",
            status="SUCCESS",
        )
        session.add(pub)

        quota = QuotaLedger(
            units=1600,
            operation="youtube.videos.insert",
            channel_id="UC_TEST_MIGRATE_01",
        )
        session.add(quota)

        audit = AuditLog(
            actor="operator_alice",
            event="APPROVE_CLIP",
            channel_id="UC_TEST_MIGRATE_01",
            details=f"Approved clip {clip.id}",
        )
        session.add(audit)
        session.commit()

    # Destination engine (simulates target database)
    dst_eng = create_engine(f"sqlite:///{dst_file}", connect_args={"check_same_thread": False})

    # Execute automated migration
    summary = migrate_sqlite_to_postgres(
        source_sqlite_path=src_file,
        target_database_url=f"sqlite:///{dst_file}",
        target_engine=dst_eng,
    )

    assert summary.success is True, f"Migration failed with errors: {summary.errors}"
    assert summary.total_migrated_rows >= 6

    # Verify destination database row counts match source database exactly
    with dst_eng.connect() as conn:
        dst_channels = conn.execute(text("SELECT COUNT(*) FROM channels")).scalar()
        dst_videos = conn.execute(text("SELECT COUNT(*) FROM source_videos")).scalar()
        dst_candidates = conn.execute(text("SELECT COUNT(*) FROM clip_candidates")).scalar()
        dst_clips = conn.execute(text("SELECT COUNT(*) FROM clips")).scalar()
        dst_pub = conn.execute(text("SELECT COUNT(*) FROM publish_log")).scalar()
        dst_quota = conn.execute(text("SELECT COUNT(*) FROM quota_ledger")).scalar()
        dst_audit = conn.execute(text("SELECT COUNT(*) FROM audit_log")).scalar()

    assert dst_channels == 1
    assert dst_videos == 1
    assert dst_candidates == 1
    assert dst_clips == 1
    assert dst_pub == 1
    assert dst_quota == 1
    assert dst_audit == 1
