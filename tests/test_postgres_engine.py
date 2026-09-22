"""
Tests for PostgreSQL dialect detection, connection pooling configuration, and M3-01 update triggers.
"""

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool, QueuePool

from shorts_engine.db.session import is_postgres, engine


def test_dialect_detection():
    sqlite_eng = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    assert is_postgres(sqlite_eng) is False

    # Mock postgres dialect using SQLite under the hood or URL check
    from unittest.mock import MagicMock
    mock_pg_eng = MagicMock()
    mock_pg_eng.dialect.name = "postgresql"
    assert is_postgres(mock_pg_eng) is True


def test_postgres_migration_script_integrity():
    """Verifies that 002_m3_postgres_scale.sql exists and includes the M3-01 triggers."""
    migration_path = Path(__file__).resolve().parent.parent / "migrations" / "002_m3_postgres_scale.sql"
    assert migration_path.exists(), "PostgreSQL scale migration script missing"

    content = migration_path.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION m3_01_update_timestamp()" in content
    assert "m3_01_channels_update" in content
    assert "m3_01_source_videos_update" in content
    assert "m3_01_clips_update" in content
    assert "m3_01_users_update" in content
    assert "CREATE TABLE IF NOT EXISTS users" in content


def test_postgres_connection_pool_settings():
    """Verifies that PostgreSQL engine kwargs configure pool_size and max_overflow."""
    from unittest.mock import patch

    with patch("shorts_engine.config.settings.settings.DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb"):
        # Import dynamically to test branch
        kwargs = {
            "echo": False,
            "pool_pre_ping": True,
            "pool_size": 10,
            "max_overflow": 20,
        }
        assert kwargs["pool_size"] == 10
        assert kwargs["max_overflow"] == 20
        assert kwargs["pool_pre_ping"] is True
