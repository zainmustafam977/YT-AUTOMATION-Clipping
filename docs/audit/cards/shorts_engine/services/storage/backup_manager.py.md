# Assessment Cards: shorts_engine/services/storage/backup_manager.py

---

## Unit 1: DatabaseBackupManager.__init__
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/backup_manager.py:28-36` |
| What it does | Initializes backup storage directory under `storage/backups` and sets retention limit (defaults to `settings.MAX_BACKUP_RETENTION` or 10). |
| Industrial-grade? | **A** - Auto-creates directory tree safely. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (`mkdir(parents=True, exist_ok=True)`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: DatabaseBackupManager.create_backup
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/backup_manager.py:38-117` |
| What it does | Executes hot online snapshot via native `sqlite3.Connection.backup` API (or supervised `pg_dump` for Postgres), applies Gzip-9 compression, computes SHA256 checksum manifest, and rotates expired backups. |
| Industrial-grade? | **A-** - Native SQLite online backup API avoids read locks and guarantees transactional consistency under active WAL mode. |
| Checklist | **Error handling:** Pass (catches failures, cleans temp file). **Timeouts:** Pass (30s SQLite connect, 300s pg_dump watchdog). **Retries:** N/A. **Idempotency:** Pass (timestamp-based filenames). **Input validation:** Pass. **Resource cleanup:** Pass (closes connections in finally, unlinks temp). **Concurrency safety:** Pass (online backup does not block active readers/writers). **Atomicity:** Pass. **External-dependency:** Pass (`sqlite3`, `pg_dump`). **Security:** Pass. **Performance:** Pass (pages=100 chunks). **Observability:** Pass (returns rich metadata with duration and size). **Config:** Pass (`settings.DATABASE_URL`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | In PostgreSQL branch, `pg_dump` output is buffered in Python memory (`res.stdout`) before gzip compression; on large multi-GB databases, this risks OOM. |
| Improvement | For PostgreSQL, stream `pg_dump` subprocess stdout directly to a file handle using `subprocess.Popen(stdout=f_out)` or shell pipe. |
| Better approach? | Option 1: Stream `pg_dump` stdout directly to gzip file handle (Recommended for multi-GB scale). Option 2: Memory buffer (Current - acceptable for small/medium DBs). |
| Priority / Effort | P2 / S |

---

## Unit 3: DatabaseBackupManager.list_backups & rotate_backups
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/backup_manager.py:119-154` |
| What it does | Enumerates existing backup archives by creation date (newest first) and unlinks archives exceeding retention threshold alongside their `.sha256` checksum files. |
| Industrial-grade? | **A** - Automated storage pruning prevents disk exhaustion. |
| Checklist | **Error handling:** Pass (logs warning on file deletion errors). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (unlinks archive + checksum). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (logs count of purged backups). **Config:** Pass (`max_retention`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: DatabaseBackupManager.restore_backup & _compute_sha256
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/backup_manager.py:156-197` |
| What it does | Restores an SQLite database archive after cryptographic SHA256 integrity verification, automatically taking a pre-restore safety copy of the live database prior to replacement. |
| Industrial-grade? | **A** - Safety snapshot (`.pre_restore_<timestamp>`) prevents catastrophic data loss if restore is aborted or archive is corrupt. |
| Checklist | **Error handling:** Pass (validates file existence, verifies checksum). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Warning (live DB connections must be closed prior to restore). **Atomicity:** Pass (safety snapshot created before overwrite). **External-dependency:** Pass. **Security:** Pass (verifies SHA256 manifest). **Performance:** Pass (streaming 64KB chunks). **Observability:** Pass (detailed log trail). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If the live SQLite database is in WAL mode, replacing the base `.db` file without clearing or checkpointing `.db-wal` and `.db-shm` can lead to corrupt reads if active connections linger. |
| Improvement | Explicitly remove or archive adjacent `-wal` and `-shm` files during SQLite restoration. |
| Better approach? | Option 1: Safety copy + clear stale WAL/SHM files (Recommended). Option 2: Overwrite base `.db` only (Risks WAL desynchronization). |
| Priority / Effort | P2 / S |
