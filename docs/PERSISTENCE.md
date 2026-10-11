# Database State Persistence & Architecture Map

This document establishes the ground truth for how every component in the YouTube Shorts Automation Platform persists state, configures database pragmas, and manages concurrency across processes.

---

## 1. Ground Truth Architecture

- **Active Engine**: **SQLite** (Dialect: `sqlite`).
- **Database Path**: `storage/shorts_automation.db` (configured via `PipelineSettings.DATABASE_URL="sqlite:///./storage/shorts_automation.db"`).
- **PostgreSQL Readiness**: The codebase maintains PostgreSQL compatibility in `session.py` (with `SELECT ... FOR UPDATE SKIP LOCKED` and automated `updated_at` triggers) and `docker-compose.yml`, but local and standard runtime execution operates against SQLite.

---

## 2. Component Persistence Map

| Component / Subsystem | Primary Owner | Database Table(s) | Key Columns & Stored State | Survives Restart? |
| :--- | :--- | :--- | :--- | :--- |
| **Channel Watch & RSS Poller** | `DetectionService`, `RSSPollLoop` | `channels`, `source_videos` | `channels.watch_enabled`, `last_seen_video_id`, `source_videos.status` | **Yes** |
| **Video Ingestion & Hotfolder** | `IngestionService`, `hotfolder_watcher` | `source_videos` | `youtube_video_id`, `status` (`DETECTED`, `DOWNLOADING`, `DONE`), `runtime_overrides_json` | **Yes** |
| **Transcription & LLM Hooks** | `TranscriptionService`, `ClipSelector` | `transcripts`, `clip_candidates` | `transcripts.segments_json`, `clip_candidates.score`, `hook_text`, `payoff_text` | **Yes** |
| **Worker Queue & Rendering** | `WorkerRunner` | `clips` | `status` (`PENDING`, `RENDERING`), `rendered_path`, `retry_count` | **Yes** |
| **Quality Gate & Review Queue** | `QualityGateManager`, Dashboard Routes | `clips`, `audit_log` | `status` (`WAITING_APPROVAL`, `APPROVED`, `REJECTED`), `quality_check_result` (JSON report) | **Yes** |
| **Drip Feed Scheduler** | `DripFeedDaemon`, `/api/clips/{id}/schedule` | `clips`, `publish_jobs` | `clips.scheduled_publish_at`, `publish_jobs.scheduled_at`, `schedule_mode` | **Yes** |
| **YouTube Publishing Client** | `YouTubePublisher`, `PublishOrchestrator` | `publish_log`, `publish_jobs`, `platform_publish_logs` | `youtube_video_id`, `published_at`, `status`, `upload_session_uri` | **Yes** |
| **Quota Telemetry** | `QuotaTracker` | `quota_ledger` | `operation`, `units`, `ts` (append-only ledger) | **Yes** |
| **API Key Vault & Auth** | `KeyVault`, `AuthService` | `api_keys`, `users`, `channel_credentials` | `api_key_encrypted`, `password_hash`, `encrypted_oauth_token` | **Yes** |
| **In-Flight Progress Telemetry** | `JobProgressTracker` | *In-Memory Only* | Ephemeral download/encode percentages, speeds, and ETAs | **No** (recalculates on resume) |
| **SSE Event Stream** | `EventBus` | *In-Memory Only* | Ring buffer of up to 500 recent events for live dashboard SSE | **No** (persistent events go to `audit_log`) |

---

## 3. SQLite Concurrency & PRAGMA Configuration

Every connection opened by `shorts_engine.db.session.engine` enforces the following SQLite pragmas via connection event listeners:

```python
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()
```

### Pragma Rationale

1. **`PRAGMA journal_mode=WAL` (Write-Ahead Logging)**:
   - Separates readers from writers. Readers never block writers, and writers never block readers.
   - Allows concurrent read transactions across the dashboard, metrics, and background worker threads without contention.
2. **`PRAGMA synchronous=NORMAL`**:
   - Reduces disk synchronization calls in WAL mode while maintaining full ACID safety against application crashes.
3. **`PRAGMA busy_timeout=30000` (30 seconds)**:
   - When a process attempts a write transaction while another write is active, SQLite does not immediately return `sqlite3.OperationalError: database is locked`.
   - Instead, it automatically retries with backoff for up to 30,000 milliseconds before timing out.
4. **`PRAGMA foreign_keys=ON`**:
   - Enforces relational referential integrity across all tables.

---

## 4. Multi-Process Limitations & Scale Guidelines

### SQLite Concurrency Boundaries

While SQLite with WAL mode handles multiple concurrent readers effortlessly, **SQLite permits exactly ONE writer transaction at any given moment**.

- **Safe Concurrency Model (Current)**:
  - 1 Web Dashboard process (`uvicorn`) reading status and queue items, with occasional operator write commits.
  - 1 Background Worker process (`cli.py worker`) claiming clips and updating render status.
  - 1 RSS Poller / Hotfolder process discovering new uploads.
  - `busy_timeout=30000` guarantees that brief transient write collisions resolve cleanly without errors.
- **Horizontal Scale Limit**:
  - Running more than 2-3 parallel high-throughput worker processes writing simultaneously to SQLite can saturate the write lock and trigger lock wait timeouts.

### Migration Path to PostgreSQL

When horizontal scaling (multiple worker nodes or high-concurrency background ingestion) is required:
1. Launch PostgreSQL 16 (e.g. via `docker-compose up -d postgres`).
2. Export `DATABASE_URL=postgresql://ytuser:password@localhost:5432/ytshorts`.
3. Migrate existing SQLite data using the automated migration tool:
   ```bash
   python scripts/migrate_sqlite_to_postgres.py \
       --sqlite-path storage/shorts_automation.db \
       --target-url postgresql://ytuser:password@localhost:5432/ytshorts
   ```
4. The platform automatically activates non-blocking row-level locking (`SELECT ... FOR UPDATE SKIP LOCKED` in `WorkerRunner`) and PostgreSQL trigger functions for seamless parallel scaling across arbitrary worker instances.

---

## 5. Distributed Worker Claiming & Stale Claim Recovery

In multi-node or multi-container clusters:
- **Worker Identification**: Every worker node generates a host-scoped ID: `worker-<hostname>-<pid>-<uuid>`.
- **Claim Token Format**: Claims are marked in `Clip.rendered_path` as `CLAIMED:<hostname>:<pid>:<uuid>`.
- **Cross-Host Safety**:
  - If a claim was made by a worker on the *same host*, local process checks (`is_pid_alive(pid)`) immediately detect crashes and release the claim.
  - If a claim was made by a worker on a *different host/container*, local OS PID checks are bypassed to prevent false-positive claim stealing. Reclaiming occurs strictly after `stale_timeout_sec` (default 600s) of inactivity.

