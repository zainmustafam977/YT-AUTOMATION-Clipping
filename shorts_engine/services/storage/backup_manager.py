"""
==============================================================================
Atomic Online Database Backup & Disaster Recovery Manager
==============================================================================
Provides non-blocking, online hot database backups for production:
  - SQLite: Uses native sqlite3.Connection.backup() to take a consistent
    in-memory snapshot without locking or corrupting WAL mode tables.
  - PostgreSQL: Streams atomic compressed dumps via pg_dump.
  - Automated gzip compression, SHA-256 integrity checksumming,
    and automatic 7-day backup retention rotation.
==============================================================================
"""

import os
import sys
import time
import gzip
import shutil
import hashlib
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


class DatabaseBackupManager:
    """
    Orchestrates atomic hot database backups, verification, rotation, and restoration.
    """

    def __init__(self, backup_dir: Optional[Path] = None, max_retention: int = 7):
        self.backup_dir = backup_dir or (Path(settings.STORAGE_DIR) / "backups")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.max_retention = max_retention

    def create_backup(self, compress: bool = True) -> Dict[str, Any]:
        """
        Creates an atomic hot backup of the current database.
        Returns metadata dictionary containing backup path, size, SHA-256, and duration.
        """
        db_url = settings.DATABASE_URL
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        start_time = time.monotonic()

        if db_url.startswith("sqlite"):
            # Extract SQLite database file path from URI
            # Handles 'sqlite:///./storage/shorts_automation.db' or 'sqlite:///storage/shorts_automation.db'
            raw_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
            source_db_path = Path(raw_path).resolve()

            if not source_db_path.exists():
                raise FileNotFoundError(f"Source SQLite database not found at: {source_db_path}")

            temp_dest = self.backup_dir / f"backup_temp_{timestamp}.db"
            final_dest = self.backup_dir / f"shorts_backup_{timestamp}.db{'.gz' if compress else ''}"

            logger.info("Starting online SQLite hot backup from %s...", source_db_path)

            # Use native SQLite Online Backup API (non-blocking snapshot under WAL)
            src_conn = sqlite3.connect(str(source_db_path), timeout=30.0)
            dst_conn = sqlite3.connect(str(temp_dest))
            try:
                with dst_conn:
                    src_conn.backup(dst_conn, pages=100, progress=None)
            finally:
                dst_conn.close()
                src_conn.close()

            # Gzip compress if requested
            if compress:
                with open(temp_dest, "rb") as f_in, gzip.open(final_dest, "wb", compresslevel=9) as f_out:
                    shutil.copyfileobj(f_in, f_out)
                if temp_dest.exists():
                    temp_dest.unlink()
            else:
                shutil.move(str(temp_dest), str(final_dest))

        elif db_url.startswith("postgresql") or db_url.startswith("postgres"):
            # PostgreSQL dump via pg_dump
            final_dest = self.backup_dir / f"shorts_backup_{timestamp}.sql.gz"
            from shorts_engine.services.execution.watchdog import SubprocessWatchdog
            cmd = ["pg_dump", "--dbname", db_url, "--clean", "--if-exists", "--no-owner"]
            res = SubprocessWatchdog.run(cmd, timeout_sec=300.0, check=True)
            with gzip.open(final_dest, "wt", encoding="utf-8", compresslevel=9) as f_out:
                f_out.write(res.stdout)
        else:
            raise NotImplementedError(f"Backup not supported for dialect in: {db_url}")

        duration_sec = round(time.monotonic() - start_time, 2)
        size_bytes = final_dest.stat().st_size
        sha256 = self._compute_sha256(final_dest)

        # Write checksum file
        checksum_file = Path(f"{final_dest}.sha256")
        checksum_file.write_text(f"{sha256}  {final_dest.name}\n", encoding="utf-8")

        # Rotate older backups to respect retention ceiling
        purged = self.rotate_backups()

        meta = {
            "status": "success",
            "path": str(final_dest),
            "filename": final_dest.name,
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "sha256": sha256,
            "duration_sec": duration_sec,
            "timestamp": timestamp,
            "purged_old_backups": purged,
        }
        logger.info("Database backup created successfully: %s (%.2f MB in %.2fs)", final_dest.name, meta["size_mb"], duration_sec)
        return meta

    def list_backups(self) -> List[Dict[str, Any]]:
        """Lists all existing backup archives sorted by creation date (newest first)."""
        backups = []
        for p in sorted(self.backup_dir.glob("shorts_backup_*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.suffix == ".sha256":
                continue
            backups.append({
                "path": str(p),
                "filename": p.name,
                "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
                "created_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return backups

    def rotate_backups(self) -> int:
        """Removes backups exceeding the retention limit. Returns count of purged files."""
        all_backups = sorted(
            [p for p in self.backup_dir.glob("shorts_backup_*") if p.suffix != ".sha256"],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        purged_count = 0
        if len(all_backups) > self.max_retention:
            for old_backup in all_backups[self.max_retention:]:
                try:
                    old_backup.unlink()
                    sha_file = Path(f"{old_backup}.sha256")
                    if sha_file.exists():
                        sha_file.unlink()
                    purged_count += 1
                    logger.info("Purged expired backup: %s", old_backup.name)
                except Exception as ex:
                    logger.warning("Could not delete expired backup %s: %s", old_backup, ex)

        return purged_count

    def restore_backup(self, backup_file: Path, target_db_path: Optional[Path] = None) -> bool:
        """
        Restores an SQLite database from a backup file with checksum verification.
        """
        if not backup_file.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_file}")

        # Checksum verification if .sha256 exists
        sha_file = Path(f"{backup_file}.sha256")
        if sha_file.exists():
            expected = sha_file.read_text(encoding="utf-8").split()[0].strip()
            actual = self._compute_sha256(backup_file)
            if expected != actual:
                raise ValueError(f"Integrity checksum mismatch! Expected {expected}, got {actual}")

        raw_path = settings.DATABASE_URL.replace("sqlite:///", "").replace("sqlite://", "")
        target = target_db_path or Path(raw_path).resolve()

        # Backup the current database before overwriting
        if target.exists():
            safety_copy = target.parent / f"{target.name}.pre_restore_{int(time.time())}"
            shutil.copy2(target, safety_copy)
            logger.info("Saved safety snapshot before restore: %s", safety_copy)

        logger.info("Restoring database from %s to %s...", backup_file, target)
        if backup_file.name.endswith(".gz"):
            with gzip.open(backup_file, "rb") as f_in, open(target, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy2(backup_file, target)

        logger.info("Database successfully restored from %s", backup_file.name)
        return True

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """Computes SHA-256 hash of a file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
