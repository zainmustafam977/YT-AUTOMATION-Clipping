# Runbook (R4-7.1)

Day-2 operations for a single-node install. Every section has a
"success looks like" and a "what can go wrong" bullet.

## Health

```bash
# Is the dashboard up?
curl -fsS http://localhost:8000/health

# Run the post-deploy smoke (R4-6.3).
python -m tools.smoke --base-url http://localhost:8000

# Run with live test (owner only, on the owner's machine).
SHORTS_ENGINE_LIVE_CONFIRM="yes-i-am-the-owner" \
  python -m tools.smoke --base-url http://localhost:8000 --live
```

Success: all 6 steps OK, smoke exits 0.

What can go wrong:

* `/health` returns 5xx → check `tail -f storage/logs/*.log` for the
  underlying exception; usually DB locked or auth misconfigured.
* `/ready` returns 503 → DB not migrated; run
  `python -m tools.smoke --skip-deps` and then
  `python -c "from shorts_engine.db.session import init_db; init_db()"`.

## Backup

```bash
sqlite3 storage/shorts_automation.db ".backup storage/backup.db"
# or:
python -c "import shutil; shutil.copy('storage/shorts_automation.db', 'storage/backup.db')"
```

Note: SQLite WAL means you should also copy `-wal` and `-shm` files
during a live backup, or use `.backup` (which is safe under load).

## Restore

```bash
python -c "from shorts_engine.db.session import init_db; init_db()"
sqlite3 storage/shorts_automation.db ".restore storage/backup.db"
```

Schedule weekly restore-drills (R4-8.2) in CI to verify backups.

## Key rotation

See `secrets-handling.md` for full procedure. Summary:

1. Generate a new Fernet key with
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Re-encrypt every secret with the new key (the `key_vault.rewrap_all`
   helper).
3. Update `SECRETS_MASTER_KEY` in `.env` and restart.
4. Surface key age in `needs-attention` (R4-8.3).

## Log triage

* `shorts_engine.dashboard` — HTTP access + auth
* `shorts_engine.orchestration` — pipeline progression
* `shorts_engine.services.webhooks` — delivery + dead-letter
* `shorts_engine.services.ai` — model router + key vault

A daily 7-day retention is applied to event_logs. Increase via
`EVENT_LOG_RETENTION_DAYS` if you have disk.

## Common incidents

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Pipeline jobs stuck "RUNNING" | worker crashed | `triage_dashboard` shows them; they auto-recover on next boot via reconcile_stale_claims |
| OAuth 401 on upload | refresh token revoked | needs-attention: re-authorise via the channel page |
| Daily quota exhausted | too many publishes | `quota_ledger` shows usage; lower `PUBLISH_SLOTS_PER_DAY` |
| FFMPEG OOM kill | clip too long or audio codec unsupported | triage surfaces; check `ffmpeg -version` + reduce `MAX_SEGMENT_DURATION_SEC` |
| SQLite "database is locked" | long write txn | R4-2.2 retry-on-busy is on by default; check `journal_mode=WAL` |
| Watch folder not picking up | folder renamed or permission changed | `WATCH_FOLDER_PATH` in `.env`; verify with `ls -la` |

## Capacity planning

See `tools/benchmark.py` output. As a rule of thumb (R4-9.1):

* 4-core CPU: ~5 minutes of source video per minute of processing
  (libx264 ultrafast, no GPU).
* With NVENC/QSV/AMF: ~2 minutes of source per minute (3-5x speedup).
* 1 channel × 4 publishes/day = ~50% of default quota; 2 channels
  is the safe ceiling without explicit quota increase.
