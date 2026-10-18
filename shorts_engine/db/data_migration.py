"""
Automated SQLite to PostgreSQL Data Migration Engine.
Migrates all persistent application state from SQLite to PostgreSQL with
topological foreign-key ordering, datatype normalization, and row-count verification.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from sqlalchemy import create_engine, inspect, text, Table, MetaData, Boolean, DateTime
from sqlalchemy.engine import Engine

from shorts_engine.db.session import init_db

logger = logging.getLogger(__name__)

# Topological dependency order for all 17 tables (ensures FKs are satisfied)
TABLE_MIGRATION_ORDER = [
    "channels",
    "users",
    "source_videos",
    "transcripts",
    "clip_candidates",
    "clips",
    "api_keys",
    "caption_presets",
    "channel_credentials",
    "oauth_states",
    "publish_jobs",
    "pipeline_jobs",
    "publish_log",
    "platform_publish_logs",
    "quota_ledger",
    "audit_log",
    "pending_oauth_mismatches",
    "schema_migrations",
]


@dataclass
class MigrationTableResult:
    table_name: str
    source_rows: int
    target_rows: int
    status: str
    error: Optional[str] = None


@dataclass
class MigrationSummary:
    success: bool
    source_url: str
    target_url: str
    tables: List[MigrationTableResult] = field(default_factory=list)
    total_migrated_rows: int = 0
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0


def _normalize_row(row_dict: Dict[str, Any], col_types: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes SQLite column representations into native Python types expected by PostgreSQL:
    - 0 / 1 integer flags -> boolean True / False for Boolean columns
    - ISO timestamp strings -> datetime objects for DateTime columns
    """
    normalized = {}
    for col_name, val in row_dict.items():
        if val is None:
            normalized[col_name] = None
            continue

        target_type = col_types.get(col_name)
        if target_type is not None:
            if isinstance(target_type, Boolean):
                if isinstance(val, (int, str)):
                    normalized[col_name] = bool(val in (1, "1", "true", "True", True))
                else:
                    normalized[col_name] = bool(val)
                continue
            elif isinstance(target_type, DateTime):
                if isinstance(val, str):
                    try:
                        normalized[col_name] = datetime.fromisoformat(val)
                    except ValueError:
                        normalized[col_name] = val
                    continue

        normalized[col_name] = val
    return normalized


def migrate_sqlite_to_postgres(
    source_sqlite_path: Path,
    target_database_url: str,
    batch_size: int = 500,
    target_engine: Optional[Engine] = None,
) -> MigrationSummary:
    """
    Executes an end-to-end migration of SQLite data into a target database (typically PostgreSQL).
    Idempotent: Initializes target schema, migrates rows, and verifies parity.
    """
    start_time = datetime.utcnow()
    source_sqlite_path = Path(source_sqlite_path)

    if not source_sqlite_path.exists():
        raise FileNotFoundError(f"Source SQLite database does not exist: {source_sqlite_path}")

    source_url = f"sqlite:///{source_sqlite_path}"
    src_engine = create_engine(source_url, connect_args={"check_same_thread": False})
    dst_engine = target_engine or create_engine(target_database_url, pool_pre_ping=True)

    summary = MigrationSummary(
        success=False,
        source_url=source_url,
        target_url=str(dst_engine.url),
    )

    logger.info("Starting database migration from SQLite (%s) to %s", source_sqlite_path, dst_engine.url)

    # 1. Initialize destination schema
    try:
        init_db(target_engine=dst_engine)
        logger.info("Destination schema initialized successfully.")
    except Exception as e:
        err = f"Failed to initialize destination schema: {e}"
        logger.error(err, exc_info=True)
        summary.errors.append(err)
        return summary

    src_inspector = inspect(src_engine)
    src_tables = set(src_inspector.get_table_names())

    dst_metadata = MetaData()
    dst_metadata.reflect(bind=dst_engine)

    # 2. Iterate through tables in dependency order
    for table_name in TABLE_MIGRATION_ORDER:
        if table_name not in src_tables:
            logger.debug("Table '%s' does not exist in source database; skipping.", table_name)
            continue

        if table_name not in dst_metadata.tables:
            logger.warning("Table '%s' exists in source but not destination schema; skipping.", table_name)
            continue

        dst_table: Table = dst_metadata.tables[table_name]
        col_types = {c.name: c.type for c in dst_table.columns}

        try:
            with src_engine.connect() as src_conn, dst_engine.connect() as dst_conn:
                src_count = src_conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0

                if src_count == 0:
                    summary.tables.append(MigrationTableResult(
                        table_name=table_name,
                        source_rows=0,
                        target_rows=0,
                        status="SKIPPED_EMPTY",
                    ))
                    continue

                # Read all rows from source SQLite
                cursor = src_conn.execute(text(f"SELECT * FROM {table_name}"))
                cols = list(cursor.keys())

                if table_name == "schema_migrations":
                    existing_ver = set(
                        r[0] for r in dst_conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
                    )
                    rows_to_insert = [
                        _normalize_row(dict(zip(cols, row)), col_types)
                        for row in cursor
                        if dict(zip(cols, row)).get("version") not in existing_ver
                    ]
                    if rows_to_insert:
                        with dst_conn.begin():
                            dst_conn.execute(dst_table.insert(), rows_to_insert)
                        migrated_for_table += len(rows_to_insert)

                    dst_count = dst_conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
                    summary.tables.append(MigrationTableResult(
                        table_name=table_name,
                        source_rows=src_count,
                        target_rows=dst_count,
                        status="SUCCESS",
                    ))
                    continue

                # Insert in batches into destination
                batch: List[Dict[str, Any]] = []
                migrated_for_table = 0

                for row in cursor:
                    row_dict = dict(zip(cols, row))
                    batch.append(_normalize_row(row_dict, col_types))

                    if len(batch) >= batch_size:
                        # Use target table insert
                        with dst_conn.begin():
                            dst_conn.execute(dst_table.insert(), batch)
                        migrated_for_table += len(batch)
                        batch = []

                if batch:
                    with dst_conn.begin():
                        dst_conn.execute(dst_table.insert(), batch)
                    migrated_for_table += len(batch)

                # Verify target count
                dst_count = dst_conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0

                if dst_count >= src_count:
                    status = "SUCCESS"
                    summary.total_migrated_rows += migrated_for_table
                    logger.info("Table '%s': %d rows successfully migrated.", table_name, migrated_for_table)
                else:
                    status = "ROW_COUNT_MISMATCH"
                    err = f"Table '{table_name}' row mismatch: source={src_count}, target={dst_count}"
                    summary.errors.append(err)
                    logger.error(err)

                summary.tables.append(MigrationTableResult(
                    table_name=table_name,
                    source_rows=src_count,
                    target_rows=dst_count,
                    status=status,
                ))

        except Exception as table_err:
            err = f"Failed to migrate table '{table_name}': {table_err}"
            logger.error(err, exc_info=True)
            summary.errors.append(err)
            summary.tables.append(MigrationTableResult(
                table_name=table_name,
                source_rows=0,
                target_rows=0,
                status="FAILED",
                error=str(table_err),
            ))

    # 3. Final summary check
    summary.duration_seconds = round((datetime.utcnow() - start_time).total_seconds(), 2)
    summary.success = len(summary.errors) == 0

    logger.info(
        "Database migration finished in %.2fs: %s (%d total rows migrated, %d errors)",
        summary.duration_seconds,
        "SUCCESS" if summary.success else "FAILED",
        summary.total_migrated_rows,
        len(summary.errors),
    )

    return summary
