import logging
import pytest
from sqlalchemy import create_engine, text, inspect
from shorts_engine.db.session import ensure_schema_upgrades


def test_ensure_schema_upgrades_raises_on_actual_sql_failure(monkeypatch):
    """ensure_schema_upgrades must not silently swallow unexpected database errors."""
    engine = create_engine("sqlite:///:memory:")

    # Create dummy source_videos table
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE source_videos (id VARCHAR(36) PRIMARY KEY, youtube_video_id VARCHAR(64))"))
        conn.commit()

    # Monkeypatch text or connection execute to raise an unexpected error during migration
    from unittest.mock import patch

    with patch("sqlalchemy.engine.base.Connection.execute", side_effect=Exception("Database disk corrupted or syntax error")):
        with pytest.raises(RuntimeError, match="Schema upgrade failed"):
            ensure_schema_upgrades(engine)


def test_duplicate_youtube_video_id_prevents_unique_index_and_logs_error(caplog):
    """When duplicate youtube_video_ids exist, log ERROR with IDs, do not create unique index, and boot cleanly."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE source_videos (
                id VARCHAR(36) PRIMARY KEY,
                channel_id VARCHAR(36),
                youtube_video_id VARCHAR(64),
                title VARCHAR(500),
                duration_sec INTEGER,
                status VARCHAR(17),
                retry_count INTEGER DEFAULT 0,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        # Insert duplicate youtube_video_id
        conn.execute(text("INSERT INTO source_videos (id, youtube_video_id, status) VALUES ('1', 'dup_123', 'DETECTED')"))
        conn.execute(text("INSERT INTO source_videos (id, youtube_video_id, status) VALUES ('2', 'dup_123', 'DETECTED')"))
        conn.commit()

    with caplog.at_level(logging.ERROR):
        ensure_schema_upgrades(engine)

    # Must log error with duplicate ID
    assert "dup_123" in caplog.text

    # Unique index uq_source_videos_youtube_id must NOT exist
    inspector = inspect(engine)
    indexes = [idx["name"] for idx in inspector.get_indexes("source_videos")]
    assert "uq_source_videos_youtube_id" not in indexes


def test_clean_db_creates_unique_index():
    """When no duplicates exist, ensure_schema_upgrades creates uq_source_videos_youtube_id unique index."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE source_videos (
                id VARCHAR(36) PRIMARY KEY,
                channel_id VARCHAR(36),
                youtube_video_id VARCHAR(64),
                title VARCHAR(500),
                duration_sec INTEGER,
                status VARCHAR(17),
                retry_count INTEGER DEFAULT 0,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        conn.execute(text("INSERT INTO source_videos (id, youtube_video_id, status) VALUES ('1', 'vid_unique', 'DETECTED')"))
        conn.commit()

    ensure_schema_upgrades(engine)

    inspector = inspect(engine)
    indexes = inspector.get_indexes("source_videos")
    idx_map = {idx["name"]: idx for idx in indexes}
    assert "uq_source_videos_youtube_id" in idx_map
    assert bool(idx_map["uq_source_videos_youtube_id"]["unique"]) is True
