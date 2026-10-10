import sqlite3
import pytest
from pathlib import Path
from shorts_engine.services.storage.backup_manager import DatabaseBackupManager


@pytest.fixture
def sample_db(tmp_path):
    """Creates a temporary SQLite database with test tables and records."""
    db_file = tmp_path / "test_app.db"
    conn = sqlite3.connect(str(db_file))
    with conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO users (name) VALUES ('alice'), ('bob');")
    conn.close()
    return db_file


def test_backup_creation_and_integrity(sample_db, tmp_path, monkeypatch):
    """Verifies that an atomic hot backup creates a valid gzipped archive with matching checksum."""
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr("shorts_engine.config.settings.settings.DATABASE_URL", f"sqlite:///{sample_db}")

    manager = DatabaseBackupManager(backup_dir=backup_dir, max_retention=3)
    meta = manager.create_backup(compress=True)

    assert meta["status"] == "success"
    assert Path(meta["path"]).exists()
    assert meta["filename"].endswith(".db.gz")
    assert meta["size_bytes"] > 0
    assert len(meta["sha256"]) == 64

    # Verify SHA256 file
    sha_file = Path(f"{meta['path']}.sha256")
    assert sha_file.exists()
    assert meta["sha256"] in sha_file.read_text(encoding="utf-8")


def test_backup_listing_and_rotation(sample_db, tmp_path, monkeypatch):
    """Verifies that list_backups reports archives and rotate_backups prunes old snapshots."""
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr("shorts_engine.config.settings.settings.DATABASE_URL", f"sqlite:///{sample_db}")

    manager = DatabaseBackupManager(backup_dir=backup_dir, max_retention=2)

    # Create 3 backups
    for _ in range(3):
        manager.create_backup(compress=True)

    backups = manager.list_backups()
    assert len(backups) <= 2  # Max retention was 2


def test_database_restore(sample_db, tmp_path, monkeypatch):
    """Verifies that database restoration cleanly unpacks data and restores table records."""
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr("shorts_engine.config.settings.settings.DATABASE_URL", f"sqlite:///{sample_db}")

    manager = DatabaseBackupManager(backup_dir=backup_dir)
    meta = manager.create_backup(compress=True)

    restore_target = tmp_path / "restored.db"
    success = manager.restore_backup(backup_file=Path(meta["path"]), target_db_path=restore_target)
    assert success is True
    assert restore_target.exists()

    # Query restored database to verify record integrity
    conn = sqlite3.connect(str(restore_target))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM users ORDER BY id;")
    rows = cursor.fetchall()
    conn.close()

    assert rows == [("alice",), ("bob",)]
