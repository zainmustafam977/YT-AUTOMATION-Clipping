import sqlite3
import pytest
from pathlib import Path

def test_phase1_migration_and_rollback_on_db_copy():
    """Verifies that migration 003 and its rollback execute cleanly on a copy of the database."""
    bak_path = Path("storage/shorts_automation.db.bak")
    db_path = bak_path if bak_path.exists() else Path("storage/shorts_automation.db")
    if not db_path.exists():
        pytest.skip("database file not found")

    src = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(":memory:")
    src.backup(dest)

    # If the source already had columns migrated, run rollback first to test cleanly
    cols = [r[1] for r in dest.execute("PRAGMA table_info(channels)").fetchall()]
    if "title" in cols:
        rollback_sql = Path("migrations/003_phase1_publishing_studio_rollback.sql").read_text(encoding="utf-8")
        for stmt in rollback_sql.split(";"):
            stmt = stmt.strip()
            clean_lines = [l for l in stmt.splitlines() if not l.strip().startswith("--")]
            clean_stmt = "\n".join(clean_lines).strip()
            if clean_stmt:
                dest.execute(clean_stmt)
        dest.commit()

    migration_sql = Path("migrations/003_phase1_publishing_studio.sql").read_text(encoding="utf-8")
    for stmt in migration_sql.split(";"):
        stmt = stmt.strip()
        clean_lines = [l for l in stmt.splitlines() if not l.strip().startswith("--")]
        clean_stmt = "\n".join(clean_lines).strip()
        if clean_stmt:
            dest.execute(clean_stmt)
    dest.commit()

    tables = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "channel_credentials" in tables
    assert "publish_jobs" in tables
    assert "quota_ledger" in tables
    assert "audit_log" in tables
    assert "oauth_states" in tables

    ch_cols = [r[1] for r in dest.execute("PRAGMA table_info(channels)").fetchall()]
    assert "title" in ch_cols
    assert "handle" in ch_cols
    assert "avatar_url" in ch_cols
    assert "last_verified_at" in ch_cols

    clip_cols = [r[1] for r in dest.execute("PRAGMA table_info(clips)").fetchall()]
    assert "draft_title" in clip_cols
    assert "draft_version" in clip_cols
    assert "source" in clip_cols

    # Rollback test
    rollback_sql = Path("migrations/003_phase1_publishing_studio_rollback.sql").read_text(encoding="utf-8")
    for stmt in rollback_sql.split(";"):
        stmt = stmt.strip()
        clean_lines = [l for l in stmt.splitlines() if not l.strip().startswith("--")]
        clean_stmt = "\n".join(clean_lines).strip()
        if clean_stmt:
            dest.execute(clean_stmt)
    dest.commit()

    tables_after = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "channel_credentials" not in tables_after
    assert "publish_jobs" not in tables_after
