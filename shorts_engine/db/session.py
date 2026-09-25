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
    try:
        inspector = inspect(eng)
        table_names = inspector.get_table_names()

        if "source_videos" in table_names:
            cols = [c["name"] for c in inspector.get_columns("source_videos")]
            if "runtime_overrides_json" not in cols:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE source_videos ADD COLUMN runtime_overrides_json TEXT"))
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

        if "platform_publish_logs" not in table_names:
            from shorts_engine.db.models import PlatformPublishLog
            PlatformPublishLog.__table__.create(eng, checkfirst=True)
    except Exception as e:
        logger.error("Database schema upgrade failed: %s", e, exc_info=True)
        raise RuntimeError(f"Schema upgrade failed: {e}") from e



def init_db(target_engine=None):
    """Initializes tables in database, applies schema upgrades, and triggers if PostgreSQL."""
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    ensure_schema_upgrades(eng)
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
        FOR tbl IN SELECT unnest(ARRAY['channels', 'source_videos', 'clips', 'users'])
        LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS m3_01_%I_update ON %I;', tbl, tbl);
            EXECUTE format('CREATE TRIGGER m3_01_%I_update BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION m3_01_update_timestamp();', tbl, tbl);
        END LOOP;
    END$$;
    """
    with eng.connect() as conn:
        conn.execute(text(trigger_sql))
        conn.commit()
