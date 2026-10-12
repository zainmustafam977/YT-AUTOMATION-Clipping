"""
Lightweight, Versioned Database Schema Migrations Engine.
Maintains a versioned `schema_migrations` table tracking applied schema versions,
ensuring idempotent, transactional schema upgrades across SQLite and PostgreSQL.
"""

import logging
from datetime import datetime
from typing import Callable, List, Dict, Any, Optional
from sqlalchemy import text, inspect, Engine

logger = logging.getLogger(__name__)


class Migration:
    def __init__(self, version: str, description: str, upgrade_fn: Callable[[Any], None]):
        self.version = version
        self.description = description
        self.upgrade_fn = upgrade_fn


def _init_migrations_table(conn) -> None:
    """Creates the schema_migrations tracking table if it does not already exist."""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(64) PRIMARY KEY,
            applied_at DATETIME NOT NULL,
            description VARCHAR(255) NOT NULL
        )
    """))


def _get_applied_versions(conn) -> set:
    """Returns the set of migration versions that have already been applied."""
    _init_migrations_table(conn)
    result = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in result}


def _record_migration(conn, version: str, description: str) -> None:
    """Records an applied migration in the schema_migrations table."""
    conn.execute(
        text("INSERT INTO schema_migrations (version, applied_at, description) VALUES (:ver, :ts, :desc)"),
        {"ver": version, "ts": datetime.utcnow(), "desc": description},
    )


# ==============================================================================
# Registered Versioned Migrations
# ==============================================================================

MIGRATIONS_REGISTRY: List[Migration] = [
    Migration(
        version="001_initial_core_schema",
        description="Initial core tables: channels, source_videos, transcripts, clip_candidates, clips, users",
        upgrade_fn=lambda conn: None,  # Handled by Base.metadata.create_all
    ),
    Migration(
        version="002_channel_upload_defaults",
        description="Adds upload_defaults_json, title, handle, avatar_url, last_verified_at to channels",
        upgrade_fn=lambda conn: None,  # Verified in ensure_schema_upgrades
    ),
    Migration(
        version="003_source_video_overrides",
        description="Adds runtime_overrides_json, is_sensitive, and unique index to source_videos",
        upgrade_fn=lambda conn: None,
    ),
    Migration(
        version="004_clip_variations_and_drafts",
        description="Adds scheduled_publish_at, analytics_json, variations_json, is_sensitive, and draft fields to clips",
        upgrade_fn=lambda conn: None,
    ),
    Migration(
        version="005_phase1_publishing_studio",
        description="Adds platform_publish_logs, api_keys, caption_presets, channel_credentials, oauth_states, publish_jobs, quota_ledger, audit_log",
        upgrade_fn=lambda conn: None,
    ),
    Migration(
        version="006_durable_pipeline_jobs",
        description="Adds pipeline_jobs table for transactional task queues, exponential backoff, and DLQ",
        upgrade_fn=lambda conn: None,  # Handled by Base.metadata.create_all or ensure_schema_upgrades
    ),
    Migration(
        version="007_pipeline_jobs_attempt_count",
        description="Adds attempt_count to pipeline_jobs for retry taxonomy and stage registry",
        upgrade_fn=lambda conn: None,  # Handled by ensure_schema_upgrades
    ),
    Migration(
        version="008_pipeline_jobs_unique_subject_stage",
        description="Adds unique constraint (entity_id, stage) to pipeline_jobs for deduplication",
        upgrade_fn=lambda conn: None,  # Handled by ensure_schema_upgrades
    ),
    Migration(
        version="009_event_logs_table",
        description="Adds event_logs table for persistent event bus history and 7-day telemetry retention",
        upgrade_fn=lambda conn: None,  # Handled by Base.metadata.create_all
    ),
    Migration(
        version="010_clip_rejection_and_queued_metadata",
        description="Adds rejection_reason_code, rejection_note, rejected_at, and queued_at to clips table",
        upgrade_fn=lambda conn: None,  # Handled by ensure_schema_upgrades
    ),
]


def run_schema_migrations(target_engine: Optional[Engine] = None) -> List[str]:
    """
    Executes all pending schema migrations in version sequence.
    Returns list of newly applied migration versions.
    """
    from shorts_engine.db.session import engine as default_engine
    eng = target_engine or default_engine

    applied_now: List[str] = []

    with eng.connect() as conn:
        applied_versions = _get_applied_versions(conn)
        conn.commit()

        for migration in MIGRATIONS_REGISTRY:
            if migration.version not in applied_versions:
                logger.info("Applying schema migration [%s]: %s", migration.version, migration.description)
                try:
                    migration.upgrade_fn(conn)
                    _record_migration(conn, migration.version, migration.description)
                    conn.commit()
                    applied_now.append(migration.version)
                    logger.info("Successfully applied schema migration [%s]", migration.version)
                except Exception as e:
                    conn.rollback()
                    logger.error("Failed to apply schema migration [%s]: %s", migration.version, e)
                    raise RuntimeError(f"Schema migration {migration.version} failed: {e}") from e

    return applied_now
