# STATE — Autonomous Hardening Working Tracker

Branch: `arena/1bfe7f4c-yt-automation-clipping`
Working dir: `/home/user/YT-AUTOMATION-Clipping`

**Single source of truth: `docs/agent/ledger.yaml`.**
Counts in this file are generated from the ledger.

## Status counts (from the ledger)

| Status | Count |
|--------|-------|
| BLOCKED | 1 |
| CONFIRMED | 2 |
| DEFERRED | 2 |
| DONE-VERIFIED | 151 |
| UNKNOWN | 15 |

## Round 4

### R4-01 — Shipped admin password removed from the default startup path `[DONE-VERIFIED]`

* Evidence: auth.py:36-49 now generates secrets.token_urlsafe(24) and logs once when DASHBOARD_PASSWORD is unset or still the shipped default; the literal is never used as a fallback. settings.py:484-489 keeps the literal only as LEGACY_DEFAULT_DASHBOARD_PASSWORD so startup can refuse it, and requires model_fields_set in staging/production/strict mode.
* Tests: `tests/test_config_consistency.py`
* Files: `shorts_engine/config/settings.py`, `shorts_engine/dashboard/auth.py`
* Commits: `fa06d19068ca07580494d16c48a6d60bdde5b10f`

### R4-02 — pip-audit converted from a never-running step to a fail-closed gate `[DONE-VERIFIED]`

* Evidence: .github/workflows/ci.yml - the audit step carried `if: false` and `continue-on-error: true`, so it was skipped on every event and its exit code was masked on top of that. Now it runs on pull_request, push to the default branch, and a weekly schedule, with no suppression. Regression-guarded: tests/test_ci_workflow_hardening.py asserts the step exists and rejects `if: false`, `continue-on-error` and `|| true`/`|| echo`. Verified by sabotage - restoring the old disabled form fails that test, and reverting it passes.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/ci.yml`
* Commits: `2a86310928d878fc6397583c2a54a2f1fe976d6e`

### R4-03 — In-memory DB isolation no longer depends on test import order `[DONE-VERIFIED]`

* Evidence: Two e2e modules ran os.environ.setdefault("DATABASE_URL", ...) at import time, so collection order decided whether 14 overlay tests bound to a file-backed engine or an empty in-memory one ("no such table: overlay_presets"). Replaced with an explicit in_memory_sqlite fixture in tests/conftest.py. Locked in by tests/test_import_order_stability.py, which runs both collection orders as subprocesses. Scope: process isolation only - per-test transaction rollback is not implemented, so a test that writes without cleaning up is still visible to a later test.
* Tests: `tests/test_import_order_stability.py`, `tests/test_overlay_routes.py`
* Files: `tests/conftest.py`, `tests/e2e/test_ui_comprehensive.py`, `tests/e2e/test_realtime_user_journey.py`
* Commits: `2a86310928d878fc6397583c2a54a2f1fe976d6e`

### R4-04 — WorkerDaemon.execute_job returns True on success instead of None `[DONE-VERIFIED]`

* Evidence: services/worker/daemon.py - the success path fell off the end of the try block, so a job marked DONE was reported to the caller as a failure. Annotated `-> bool`. Now returns True. The only in-tree caller (daemon.py:497) discards the result.
* Tests: `tests/test_worker_daemon_session_lifecycle.py`
* Files: `shorts_engine/services/worker/daemon.py`
* Commits: `a2f9549ab1cbb82177a8d137de24fe7a103fe677`

### R4-05 — Reject is now idempotent, symmetric with approve `[DONE-VERIFIED]`

* Evidence: services/publishing/actions.py:156 - reject only denylisted PUBLISHING and PUBLISHED, so a second reject returned 200 and overwrote quality_check_result.rejection_reason, destroying the audit trail. REJECTED added to the denylist. approve already used an allowlist.
* Tests: `tests/test_phase2_queue_player.py`
* Files: `shorts_engine/services/publishing/actions.py`
* Commits: `a2f9549ab1cbb82177a8d137de24fe7a103fe677`

### R4-06 — fontTools declared as a dependency `[DONE-VERIFIED]`

* Evidence: ass_generator.py:66 imports fontTools.ttLib inside a bare `except Exception -> logger.debug -> return set()`, and fontTools was in neither requirements.txt nor pyproject.toml. On a clean install every non-ASCII caption glyph silently degraded to '?'. Declared as fonttools>=4.47.0; cmap now reads 5886 entries and the product function returns 5906 codepoints. Residual defect (not fixed here): the silent-except-with-empty-fallback pattern that hid this is itself a defect and affects other call sites.
* Tests: `tests/test_clip_production_fixes.py`
* Files: `requirements.txt`, `pyproject.toml`
* Commits: `a2f9549ab1cbb82177a8d137de24fe7a103fe677`

### R4-07 — Dry-run guard matched an encoded URL and never fired `[DONE-VERIFIED]`

* Evidence: services/publishing/worker.py:793 - str(URL) percent-encodes the database name, so an in-memory URL rendered as "sqlite:///%3Amemory%3A" and the substring check for ":memory:" never matched. Now also compares URL.database (unquoted). Fail-closed behaviour unchanged and still verified by test_t12_sandbox_preview_rejects_production_db_and_does_not_ consume_quota.
* Tests: `tests/test_w3_2_lifecycle.py`
* Files: `shorts_engine/services/publishing/worker.py`
* Commits: `a2f9549ab1cbb82177a8d137de24fe7a103fe677`

### R4-08 — Windows acceptance procedure made safe to execute `[BLOCKED]`

* Evidence: Corrected copy at /home/user/WINDOWS_ACCEPTANCE.fixed.md (not tracked in this repo). Sandbox moved from the system directory D:\Program Files to %LOCALAPPDATA%\YTSandbox; recursive delete guarded on a marker file; taskkill /pid on recorded PIDs replaces a blanket Stop-Process -Name python; HKLM writes moved to HKCU; section 9.3 now derives a PID instead of comparing a job UUID to a process ID, which can never match. BLOCKED: requires a disposable Windows VM. Not executed on the user's machine, by instruction. tests/test_win32_job.py::test_job_ids_are_never_process_ids guards the section 9.3 defect specifically (a UUID job id can never equal a PID), so the bad comparison cannot be reinstated silently.
* Tests: `tests/test_win32_job.py`
* Files: `/home/user/WINDOWS_ACCEPTANCE.fixed.md`
* Commits: `47e462d9cfb58fd63d5cc4195bd255963e8186ac`

### R4-09 — Windows-only tests guarded rather than left failing on Linux `[DONE-VERIFIED]`

* Evidence: Three tests asserted Win32 behaviour: two patched ctypes.windll (absent off Windows) and two asserted ERROR_SHARING_VIOLATION semantics that the POSIX fallback in watch_folder.py:136 correctly contradicts. Guarded with skipif(not sys.platform.startswith("win")). is_file_locked deliberately NOT weakened.
* Tests: `tests/test_win32_job.py`, `tests/test_local_ingestion.py`, `tests/test_watch_folder_daemon.py`
* Files: `tests/test_win32_job.py`, `tests/test_local_ingestion.py`, `tests/test_watch_folder_daemon.py`
* Commits: `47e462d9cfb58fd63d5cc4195bd255963e8186ac`

### R4-10 — Test suite repaired from 78 failures to green `[DONE-VERIFIED]`

* Evidence: Measured on this branch with a complete dependency install: `pytest -q -m "not network"` -> 1097 passed, 23 skipped, 1 deselected, exit 0. Of the 14 failures that remained mid-way: 12 were browser-backed E2E (now conditionally skipped, R4-11), 1 was a stale patch target in test_enhancements_phase3.py (fixed), and 1 was a HuggingFace model download (marked network, R4-12). The starting count of 78 was measured before the dependency environment was complete, so treat it as a starting point rather than a like-for-like baseline. Caveat: 23 tests are skipped, not executed - 18 browser-backed E2E plus 5 platform/other skips. Green here means nothing failed, not that everything ran.
* Tests: `tests/test_dashboard.py`, `tests/e2e/test_realtime_user_journey.py`, `tests/test_worker_daemon_session_lifecycle.py`, `tests/test_publish_worker_and_oauth_isolation.py`, `tests/test_local_ingestion_gateway.py`
* Files: `tests/test_dashboard.py`, `tests/e2e/test_realtime_user_journey.py`, `tests/test_worker_daemon_session_lifecycle.py`, `tests/test_publish_worker_and_oauth_isolation.py`, `tests/test_local_ingestion_gateway.py`
* Commits: `8fe30495618fd8a52d064b4f22cb9c1c3295a485`, `a2f9549ab1cbb82177a8d137de24fe7a103fe677`, `2a86310928d878fc6397583c2a54a2f1fe976d6e`

### R4-11 — Playwright E2E suite cannot run in this environment `[DEFERRED]`

* Evidence: BrowserType.launch fails with "Executable doesn't exist ... Run `playwright install chromium`". All three download routes were tried and failed: the Playwright CDN is unreachable, the apt package is not available, and no cached browser exists on the image. No product defect - the tests cannot start. DEFERRED, not DONE: the 18 tests are skipped, so they verify nothing here. They now skip with an explicit reason instead of failing, so a red build signals a real regression rather than a missing browser. Verified conditional both ways: placing a file at p.chromium.executable_path makes them run again, removing it makes them skip - so any runner that has run `playwright install chromium` executes them unchanged. Manual steps: on a host with network access run `python -m playwright install chromium`, then `pytest tests/e2e/ -q`.
* Tests: `tests/e2e/test_ui_comprehensive.py`, `tests/e2e/test_realtime_user_journey.py`
* Files: `tests/e2e/test_ui_comprehensive.py`, `tests/e2e/test_realtime_user_journey.py`
* Commits: `2a86310928d878fc6397583c2a54a2f1fe976d6e`

### R4-12 — Declared `network` marker applied for the first time `[DONE-VERIFIED]`

* Evidence: pyproject.toml declared a `network` marker ("requires network access") and CI has always run `pytest -m "not network"`, but no test in the repo carried the marker, so the exclusion selected nothing and network-bound tests failed on every offline run. tests/test_local_ingestion_gateway.py::test_pipeline_runner_processes_ local_ingested_video downloads a faster-whisper model from the HuggingFace Hub and dies with "ConnectError: TLS/SSL connection has been closed (EOF)". It is now marked @pytest.mark.network and is reported as deselected rather than failed.
* Tests: `tests/test_local_ingestion_gateway.py`
* Files: `tests/test_local_ingestion_gateway.py`
* Commits: `45574d3bc721190454179fbfc7c0f0c0cfad42ea`

### R4-13 — Stale mock target in test_enhancements_phase3.py `[DONE-VERIFIED]`

* Evidence: test_preflight_channel_discovery_registration patched "shorts_engine.dashboard.routes.status.threading.Thread", but status.py has never imported threading - the route registers the channel synchronously. The patch raised AttributeError before reaching any assertion, so the test had been failing at setup rather than testing anything. Stale target removed.
* Tests: `tests/test_enhancements_phase3.py`
* Files: `tests/test_enhancements_phase3.py`
* Commits: `45574d3bc721190454179fbfc7c0f0c0cfad42ea`

### R4-14 — Ledger gate given the git history it needs (CI-2 regression fix) `[DONE-VERIFIED]`

* Evidence: Regression introduced by the round4 rows in DOC-1 and caught by CI, not locally. check_agent_ledger.py validates every cited commit id with `git cat-file -t`, but actions/checkout@v4 defaults to fetch-depth: 1, so the runner only had the tip commit and every ancestor oid cited by a row looked invented: FAIL: row 'R4-01' cites commit 'fa06d19...' which does not exist in this repository (never invent commit ids) Reproduced exactly (clone --depth 1 -> same failure; git fetch --unshallow -> OK, exit 0). Fixed with fetch-depth: 0 on the ledger job rather than by weakening the check, which is what stops invented ids being committed. Regression-guarded by tests/test_ci_workflow_hardening.py::test_ledger_job_checks_out_full_ history; verified by sabotage - reverting to the default depth fails it.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/agent.yml`
* Commits: `2991cb7737c1eee680e032ae79419fa40b519b89`

### R4-15 — CI dependency sources diverge by five packages `[CONFIRMED]`

* Evidence: requirements.txt declares 34 packages; pyproject.toml [project.dependencies] declares 31. Only in requirements.txt: asyncpg, psycopg2-binary, starlette, tzdata. Only in pyproject: watchdog. This matters because the workflows install differently - ci.yml uses "pip install -r requirements.txt -r requirements-dev.txt", while agent.yml uses "pip install -e .[dev]" and so gets the pyproject set. Impact assessed as low: starlette arrives transitively via fastapi, and asyncpg/psycopg2/tzdata are imported nowhere in shorts_engine/ or tests/. Reported rather than silently reconciled - unifying the two sources is a packaging decision for the owner. Drift is now guarded by tests/test_packaging_dependencies.py::test_requirements_and_pyproject_ declare_the_same_dependencies, which pins the currently-known divergence; verified by sabotage (adding a package to requirements.txt alone fails it). Update the expected set and this row together.
* Tests: `tests/test_packaging_dependencies.py`
* Files: `requirements.txt`, `pyproject.toml`
* Commits: `08f0b98e8f8377f69b96b4fc5ad351cd209159f2`

### R4-16 — CI Pytest jobs still fail - root cause not identified `[DEFERRED]`

* Evidence: As of 08f0b98 all four workflows execute (they previously never ran at all - zero runs on the branch). Seven job types succeed: Security CVE Scan, Code Quality/Linting, Docker Build, Ruff Lint, mypy Ratchet, Ledger Consistency, Dependency Audit. Every Pytest job fails: Agent Gates/Pytest, CI-CD Pipeline/Lint & Test Suite, and all four Test Suite Matrix legs. I could not identify why, so this is DEFERRED, not fixed. What is ruled out by measurement, not guesswork: 1. Environment variables. Reproduced the CI condition locally (unset ALLOW_IN_PROCESS_PREVIEW, set DASHBOARD_SECRET_KEY and ENCRYPTION_KEY, pre-create the DB with init_db as the workflow does). This exposed one genuine bug, now fixed in TEST-4: the dry-run test depended on ALLOW_IN_PROCESS_PREVIEW being exported on the developer's machine. After that fix the full suite under CI's exact conditions is 1098 passed, 23 skipped, 1 deselected, exit 0. 2. Dependency plugins. pytest 9.1.1 and pytest-asyncio 1.4.0 are installed locally, matching what "pip install -e .[dev]" resolves. 3. Dependency content (see R4-15). The five-package divergence does not touch anything the tests import. Why it is not resolved: the Actions logs endpoint returns 0 bytes with the available token, and log download from results-receiver.actions.githubusercontent.com is blocked from this sandbox. No failure output could be read, so the remaining difference between the runner and this machine is unmeasured. One unverified hypothesis worth testing first: ci.yml runs "pytest tests -q" (no marker filter) with timeout-minutes: 15, so it includes the network-marked test that downloads a faster-whisper model from HuggingFace. A ~500MB download on every matrix leg under a 15 minute cap is a plausible timeout. This does NOT explain agent.yml, which already filters with -m "not network". I deliberately did not change the unfiltered commands speculatively, as that could mask a real failure. Manual steps: open the failing job in the Actions UI and read the pytest summary, or run "gh run view <id> --log-failed" from a network unrestricted machine. If the hypothesis above holds, add -m "not network" to ci.yml and ci-cd.yml to match agent.yml.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/ci.yml`, `.github/workflows/ci-cd.yml`, `.github/workflows/agent.yml`
* Commits: `08f0b98e8f8377f69b96b4fc5ad351cd209159f2`

