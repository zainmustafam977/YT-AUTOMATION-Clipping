"""
Database engine, session management, and base declarative class.
Supports SQLite (with WAL mode & busy timeout) and PostgreSQL.
"""

import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

# Engine configuration
connect_args = {}
engine_kwargs = {
    "echo": settings.DEBUG,
    "pool_pre_ping": True,
}

if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False,
        "timeout": 30.0,
    }
    if ":memory:" in settings.DATABASE_URL:
        from sqlalchemy.pool import StaticPool
        engine_kwargs["poolclass"] = StaticPool
elif settings.DATABASE_URL.startswith("postgresql") or settings.DATABASE_URL.startswith("postgres"):
    engine_kwargs.update({
        "pool_size": 10,
        "max_overflow": 20,
    })

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    **engine_kwargs,
)

# Enable SQLite foreign keys & WAL mode for concurrency
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        if ":memory:" not in settings.DATABASE_URL:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
)

Base = declarative_base()


def is_postgres(target_engine=None) -> bool:
    """Detects whether the target engine (or default engine) is PostgreSQL."""
    eng = target_engine or engine
    return eng.dialect.name == "postgresql"


def get_db():
    """Dependency / context manager generator for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_upgrades(target_engine=None):
    """Safely applies non-breaking schema column migrations and required unique indexes."""
    from sqlalchemy import inspect, text
    eng = target_engine or engine
    is_pg = is_postgres(eng)
    bool_default = "FALSE" if is_pg else "0"
    dt_type = "TIMESTAMP" if is_pg else "DATETIME"

    try:
        inspector = inspect(eng)
        table_names = inspector.get_table_names()

        if "channels" in table_names:
            cols = [c["name"] for c in inspector.get_columns("channels")]
            if "upload_defaults_json" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE channels ADD COLUMN upload_defaults_json TEXT"))
                    conn.commit()
            if "title" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE channels ADD COLUMN title VARCHAR(255)"))
                    conn.commit()
            if "handle" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE channels ADD COLUMN handle VARCHAR(255)"))
                    conn.commit()
            if "avatar_url" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE channels ADD COLUMN avatar_url VARCHAR(1024)"))
                    conn.commit()
            if "last_verified_at" not in cols:
                with eng.connect() as conn:
                    conn.execute(text(f"ALTER TABLE channels ADD COLUMN last_verified_at {dt_type}"))
                    conn.commit()

        if "source_videos" in table_names:
            cols = [c["name"] for c in inspector.get_columns("source_videos")]
            if "runtime_overrides_json" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE source_videos ADD COLUMN runtime_overrides_json TEXT"))
                    conn.commit()
            if "is_sensitive" not in cols:
                with eng.connect() as conn:
                    conn.execute(text(f"ALTER TABLE source_videos ADD COLUMN is_sensitive BOOLEAN DEFAULT {bool_default}"))
                    conn.commit()

            # Pre-flight duplicate check before creating unique index (PLAN-P1-09)
            if "youtube_video_id" in cols:
                with eng.connect() as conn:
                    dup_query = text(
                        "SELECT youtube_video_id, COUNT(*) as cnt "
                        "FROM source_videos "
                        "GROUP BY youtube_video_id "
                        "HAVING COUNT(*) > 1"
                    )
                    duplicates = conn.execute(dup_query).fetchall()
                    if duplicates:
                        dup_ids = [row[0] for row in duplicates]
                        logger.error(
                            "PRE-FLIGHT CHECK FAILED: Found duplicate youtube_video_ids in source_videos: %s. "
                            "Unique index uq_source_videos_youtube_id will NOT be created.",
                            dup_ids,
                        )
                    else:
                        indexes = [idx["name"] for idx in inspector.get_indexes("source_videos")]
                        if "uq_source_videos_youtube_id" not in indexes:
                            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_source_videos_youtube_id ON source_videos (youtube_video_id)"))
                            conn.commit()

        if "clip_candidates" in table_names:
            cols = [c["name"] for c in inspector.get_columns("clip_candidates")]
            if "variations_json" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clip_candidates ADD COLUMN variations_json TEXT"))
                    conn.commit()

        if "clips" in table_names:
            cols = [c["name"] for c in inspector.get_columns("clips")]
            if "scheduled_publish_at" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN scheduled_publish_at TIMESTAMP"))
                    conn.commit()
            if "analytics_json" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN analytics_json TEXT"))
                    conn.commit()
            if "variations_json" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN variations_json TEXT"))
                    conn.commit()
            if "is_sensitive" not in cols:
                with eng.connect() as conn:
                    conn.execute(text(f"ALTER TABLE clips ADD COLUMN is_sensitive BOOLEAN DEFAULT {bool_default}"))
                    conn.commit()
            if "draft_title" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_title VARCHAR(100)"))
                    conn.commit()
            if "draft_description" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_description TEXT"))
                    conn.commit()
            if "draft_tags" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_tags TEXT"))
                    conn.commit()
            if "draft_category_id" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_category_id VARCHAR(16)"))
                    conn.commit()
            if "draft_privacy_status" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_privacy_status VARCHAR(16)"))
                    conn.commit()
            if "draft_made_for_kids" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_made_for_kids BOOLEAN"))
                    conn.commit()
            if "draft_target_channel_id" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_target_channel_id VARCHAR(64)"))
                    conn.commit()
            if "draft_scheduled_at" not in cols:
                with eng.connect() as conn:
                    conn.execute(text(f"ALTER TABLE clips ADD COLUMN draft_scheduled_at {dt_type}"))
                    conn.commit()
            if "draft_version" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN draft_version INTEGER DEFAULT 1 NOT NULL"))
                    conn.commit()
            if "source" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE clips ADD COLUMN source VARCHAR(32) DEFAULT 'pipeline' NOT NULL"))
                    conn.commit()

        if "platform_publish_logs" not in table_names:
            from shorts_engine.db.models import PlatformPublishLog
            PlatformPublishLog.__table__.create(eng, checkfirst=True)

        if "api_keys" not in table_names:
            from shorts_engine.db.models import ApiKey
            ApiKey.__table__.create(eng, checkfirst=True)

        if "caption_presets" not in table_names:
            from shorts_engine.db.models import CaptionPreset
            CaptionPreset.__table__.create(eng, checkfirst=True)

        if "channel_credentials" not in table_names:
            from shorts_engine.db.models import ChannelCredential
            ChannelCredential.__table__.create(eng, checkfirst=True)

        if "oauth_states" not in table_names:
            from shorts_engine.db.models import OAuthState
            OAuthState.__table__.create(eng, checkfirst=True)

        if "pending_oauth_mismatches" not in table_names:
            from shorts_engine.db.models import PendingOAuthMismatch
            PendingOAuthMismatch.__table__.create(eng, checkfirst=True)

        if "publish_jobs" not in table_names:
            from shorts_engine.db.models import PublishJob
            PublishJob.__table__.create(eng, checkfirst=True)

        if "quota_ledger" not in table_names:
            from shorts_engine.db.models import QuotaLedger
            QuotaLedger.__table__.create(eng, checkfirst=True)

        if "audit_log" not in table_names:
            from shorts_engine.db.models import AuditLog
            AuditLog.__table__.create(eng, checkfirst=True)

        if "pipeline_jobs" not in table_names:
            from shorts_engine.db.models import PipelineJob
            PipelineJob.__table__.create(eng, checkfirst=True)
        else:
            cols = [c["name"] for c in inspector.get_columns("pipeline_jobs")]
            if "attempt_count" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE pipeline_jobs ADD COLUMN attempt_count INTEGER DEFAULT 0 NOT NULL"))
                    conn.commit()

            indexes = [idx["name"] for idx in inspector.get_indexes("pipeline_jobs")]
            if "uq_pipeline_jobs_entity_stage" not in indexes:
                with eng.connect() as conn:
                    try:
                        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_jobs_entity_stage ON pipeline_jobs (entity_id, stage)"))
                        conn.commit()
                    except Exception as idx_err:
                        logger.warning("Could not create uq_pipeline_jobs_entity_stage index directly: %s", idx_err)

    except Exception as e:
        logger.error("Database schema upgrade failed: %s", e, exc_info=True)
        raise RuntimeError(f"Schema upgrade failed: {e}") from e



def init_db(target_engine=None):
    """Initializes tables in database, applies schema upgrades, versioned migrations, and triggers if PostgreSQL."""
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    ensure_schema_upgrades(eng)
    from shorts_engine.db.migrations import run_schema_migrations
    run_schema_migrations(eng)
    if is_postgres(eng):
        apply_m3_01_triggers(eng)


def apply_m3_01_triggers(target_engine=None):
    """Applies M3-01 automated updated_at triggers on PostgreSQL."""
    from sqlalchemy import text
    eng = target_engine or engine
    trigger_sql = """
    CREATE OR REPLACE FUNCTION m3_01_update_timestamp()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = (NOW() AT TIME ZONE 'utc');
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DO $$
    DECLARE
        tbl text;
    BEGIN
        FOR tbl IN SELECT unnest(ARRAY['channels', 'source_videos', 'clips', 'users', 'api_keys', 'caption_presets'])
        LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS m3_01_%I_update ON %I;', tbl, tbl);
            EXECUTE format('CREATE TRIGGER m3_01_%I_update BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION m3_01_update_timestamp();', tbl, tbl);
        END LOOP;
    END$$;
    """
    with eng.connect() as conn:
        conn.execute(text(trigger_sql))
        conn.commit()
