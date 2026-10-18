# Changelog

All notable changes to YT-Shorts Automation are recorded here. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

* R3.1 mypy ratchet: 0-error baseline enforced in CI.
* R3.1 settings parsers: tolerant handling of empty / whitespace / CSV
  values for list / JSON-typed env settings (e.g. `GEMINI_API_KEYS=`).
* R3.2 batch engine: 25 batch operations (BATCH-01..25) with full
  audit, RBAC, idempotency, undo, crash-resume, and metrics-by-batch.
* R3.2 webhook engine: HMAC, signature verification, dead-letter,
  redactors, retry-with-backoff, URL-guard (SSRF).
* R3.2 cost engine: per-job roll-up, soft + hard budgets, alerts.
* R3.4 R6 overlay engine: text / image / sticker / shape layers,
  presets, export / import with SHA-256 tamper detection, batch apply
  with audit, WCAG contrast.
* R3.5 search + command palette (E-021).
* R3.5 saved views (EASE-02) with full CRUD API.
* R3.5 CLI ergonomics (E-023): `--json`, `--yes`, `--dry-run`,
  `--filter`, shell completions, exit codes.
* R3.5 content calendar (E-029) verified.
* R3.5 triage (F-15): 5 checkers, plus R4-8.7 OAuth + quota + key age.
* R3.5 caption editor (E-016) with words-level JSON.
* R3.5 A/B testing (E-017) with Wilson score.
* R3.6 cost engine (E-010) with per-job rollup.
* R3.7 file manifest matches `git ls-files` (CI gate).
* R3.7 URL-guard SSRF block in webhook deliveries + ingestion.
* R3.7 settings export / import with diff + backup.
* R3.7 secrets-handling: encrypted at rest, redacted in logs.
* R4-1.2 path handling: drive letters, spaces, long paths, non-ASCII,
  ffmpeg / ASS escaping.
* R4-1.3 `/api/clips/{clip_id}/open-folder` endpoint (Windows
  explorer / macOS Finder / Linux xdg-open).
* R4-2.1 SQLite settings verifier: WAL, busy_timeout, foreign keys,
  synchronous.
* R4-2.2 SQLite concurrency helpers: retry on SQLITE_BUSY, BEGIN
  IMMEDIATE, single-writer queue, concurrency smoke (8 workers x 50
  writes).
* R4-5.1 / R4-5.2 fallback policy + `STRICT_MODE` setting (default
  strict in production).
* R4-6.3 `tools/smoke.py`: post-deploy smoke with `--live` mode
  (owner-only, env-gated).
* R4-7.1 handover package: architecture, data model, test strategy,
  runbook.
* R4-8.1 Dependabot config: weekly pip + npm + GitHub Actions updates.
* R4-8.5 tech-debt register (TD-001..TD-012).
* R4-9.1 `tools/benchmark.py`: per-stage timing on this machine.

### Changed

* Settings module: env-source replaced with `TolerantEnvSource` to
  support empty / CSV list values without JSON errors.
* `triage` service extended with 4 new checkers (R4-8.7).

### Fixed

* `GEMINI_API_KEYS=` (empty / whitespace / CSV) no longer raises
  `JSONDecodeError` on startup.
* R3.1 webhooks: SSRF via redirect, private IPs, link-local blocked.
* R3.1 settings: cross-environment key mismatch rejected on import.

### Removed

* (none)

### Migration notes

* **Pre-upgrade backup** is required. Run
  `python -c "import shutil; shutil.copy('storage/shorts_automation.db', 'storage/backup-pre-v1.db')"`
  before pulling this version.
* New env vars: `STRICT_MODE` (default true in production/staging).
* `tools/smoke.py` is the new post-deploy gate; integrate into your
  deploy script.
* `tools/benchmark.py` should be run once on the owner's machine to
  populate the capacity guide.

### Breaking changes

* (none)

## Previous versions

See git history (`git log --oneline`) for changes prior to this file.
