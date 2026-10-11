#!/usr/bin/env python3
"""
CLI script to migrate all persistent data from SQLite to PostgreSQL.
Usage:
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite-path storage/shorts_automation.db \
        --target-url postgresql://ytuser:password@localhost:5432/ytshorts
"""

import sys
import argparse
import logging
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shorts_engine.db.data_migration import migrate_sqlite_to_postgres
from shorts_engine.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("migration_cli")


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite database to PostgreSQL")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=Path("storage/shorts_automation.db"),
        help="Path to the source SQLite database file",
    )
    parser.add_argument(
        "--target-url",
        type=str,
        default=settings.DATABASE_URL,
        help="Target PostgreSQL database URL (defaults to settings.DATABASE_URL)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Row batch size for bulk inserts (default: 500)",
    )

    args = parser.parse_args()

    sqlite_path = args.sqlite_path
    target_url = args.target_url

    if not sqlite_path.exists():
        logger.error("Source SQLite database file not found: %s", sqlite_path)
        sys.exit(1)

    print(f"\n{'='*70}")
    print("  YouTube Shorts Automation Platform - Database Migration Tool")
    print(f"{'='*70}")
    print(f"Source SQLite DB : {sqlite_path}")
    print(f"Target DB URL    : {target_url}")
    print(f"Batch Size       : {args.batch_size}")
    print(f"{'='*70}\n")

    summary = migrate_sqlite_to_postgres(
        source_sqlite_path=sqlite_path,
        target_database_url=target_url,
        batch_size=args.batch_size,
    )

    print("\nMigration Results by Table:")
    print("-" * 70)
    for res in summary.tables:
        status_symbol = "✓" if res.status == "SUCCESS" else ("-" if res.status == "SKIPPED_EMPTY" else "✗")
        print(f"  [{status_symbol}] {res.table_name:<28} Source: {res.source_rows:<6} Target: {res.target_rows:<6} ({res.status})")
        if res.error:
            print(f"      Error: {res.error}")

    print("-" * 70)
    print(f"Total Migrated Rows : {summary.total_migrated_rows}")
    print(f"Duration            : {summary.duration_seconds}s")
    print(f"Status              : {'SUCCESS' if summary.success else 'FAILED'}")
    print(f"{'='*70}\n")

    if not summary.success:
        logger.error("Migration encountered errors: %s", summary.errors)
        sys.exit(1)


if __name__ == "__main__":
    main()
