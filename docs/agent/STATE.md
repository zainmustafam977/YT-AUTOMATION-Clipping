# STATE — Autonomous Hardening Working Tracker

Branch: `arena/c08e735f-yt-automation-clipping`
Working dir: `/home/user/YT-AUTOMATION-Clipping`

**Single source of truth: `docs/agent/ledger.yaml`.**
Counts in this file are generated from the ledger.

## Status counts (from the ledger)

| Status | Count |
|--------|-------|
| BLOCKED | 1 |
| CONFIRMED | 2 |
| DEFERRED | 2 |
| DONE-VERIFIED | 162 |
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

### R4-16 — CI Pytest jobs still fail - root cause not identified `[DONE-VERIFIED]`

* Evidence: As of 08f0b98 all four workflows execute (they previously never ran at all - zero runs on the branch). Seven job types succeed: Security CVE Scan, Code Quality/Linting, Docker Build, Ruff Lint, mypy Ratchet, Ledger Consistency, Dependency Audit. Every Pytest job fails: Agent Gates/Pytest, CI-CD Pipeline/Lint & Test Suite, and all four Test Suite Matrix legs. I could not identify why, so this is DEFERRED, not fixed. What is ruled out by measurement, not guesswork: 1. Environment variables. Reproduced the CI condition locally (unset ALLOW_IN_PROCESS_PREVIEW, set DASHBOARD_SECRET_KEY and ENCRYPTION_KEY, pre-create the DB with init_db as the workflow does). This exposed one genuine bug, now fixed in TEST-4: the dry-run test depended on ALLOW_IN_PROCESS_PREVIEW being exported on the developer's machine. After that fix the full suite under CI's exact conditions is 1098 passed, 23 skipped, 1 deselected, exit 0. 2. Dependency plugins. pytest 9.1.1 and pytest-asyncio 1.4.0 are installed locally, matching what "pip install -e .[dev]" resolves. 3. Dependency content (see R4-15). The five-package divergence does not touch anything the tests import. Why it is not resolved: the Actions logs endpoint returns 0 bytes with the available token, and log download from results-receiver.actions.githubusercontent.com is blocked from this sandbox. No failure output could be read, so the remaining difference between the runner and this machine is unmeasured. One unverified hypothesis worth testing first: ci.yml runs "pytest tests -q" (no marker filter) with timeout-minutes: 15, so it includes the network-marked test that downloads a faster-whisper model from HuggingFace. A ~500MB download on every matrix leg under a 15 minute cap is a plausible timeout. This does NOT explain agent.yml, which already filters with -m "not network". I deliberately did not change the unfiltered commands speculatively, as that could mask a real failure. Manual steps: open the failing job in the Actions UI and read the pytest summary, or run "gh run view <id> --log-failed" from a network unrestricted machine. If the hypothesis above holds, add -m "not network" to ci.yml and ci-cd.yml to match agent.yml. RESOLVED (see R4-17 onward). The blocker was not knowing what failed. Once CI-4 made failures observable, the cause turned out to be one test - tests/test_fresh_eyes.py::test_ledger_consistency - failing because the checkout was shallow, not a suite-wide problem. The network-timeout hypothesis above was wrong: the unfiltered commands were never the cause. Four further genuine defects were found the same way (R4-19 to R4-23).
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/ci.yml`, `.github/workflows/ci-cd.yml`, `.github/workflows/agent.yml`
* Commits: `08f0b98e8f8377f69b96b4fc5ad351cd209159f2`

### R4-17 — Make CI failures observable (annotations + job summary) `[DONE-VERIFIED]`

* Evidence: The most valuable change of this session. Red Pytest jobs could not be diagnosed at all: runner logs return 0 bytes here and log download is network-blocked. Fixed by teeing pytest output and emitting each FAILED/ERROR line as a workflow-command annotation, readable via the check-runs annotations endpoint. $GITHUB_STEP_SUMMARY was tried first and does NOT surface in output.summary, so annotations are the channel that works. `set -o pipefail` is essential - without it the tee swallows pytest's exit code and every failure silently becomes a pass.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/agent.yml`, `.github/workflows/ci.yml`, `.github/workflows/ci-cd.yml`
* Commits: `1ab9a44f7f925f08c279ca7cc9151dbfe25978bc`, `0b6935583be8f07d2d03f631d6bed19aa2c2e942`

### R4-18 — Shallow checkout broke the ledger check in four CI jobs `[DONE-VERIFIED]`

* Evidence: One defect, four occurrences, three workflows. check_agent_ledger.py validates cited commit ids with git cat-file -t; actions/checkout@v4 defaults to fetch-depth 1 (tip only), so every ancestor oid looked fabricated. Surfaced as tests/test_fresh_eyes.py::test_ledger_consistency (which shells out to the checker) and as the Ledger Consistency job. Fixed with fetch-depth 0 in agent.yml (ledger + pytest jobs), ci.yml (test-matrix) and ci-cd.yml (ci job). Durable fix: the checker now detects a shallow repository and says so instead of accusing invented commit ids. That wording is what made four rounds read like a ledger-integrity problem rather than a CI config one.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/agent.yml`, `.github/workflows/ci.yml`, `.github/workflows/ci-cd.yml`, `tools/check_agent_ledger.py`
* Commits: `34e3727e65a09fb81f03b149dda52c4822657940`, `cf3ab736e961146ad578ff355404549f84652022`, `0a112c6b49341db5c4588ca9ae321604b26fa6ce`, `f52483c167e1dfda37665bc6bbf613389c9f7ff0`

### R4-19 — Bootstrapped installs could not import settings (blank List env vars) `[DONE-VERIFIED]`

* Evidence: pydantic-settings JSON-decodes complex fields at the dotenv source, so the empty GEMINI_API_KEYS / GROQ_API_KEYS / OPENAI_API_KEYS / OPENROUTER_API_KEYS entries in .env.example raised SettingsError before any validator ran. setup_environment.py copies that template verbatim to .env, so after the documented bootstrap the application could not be imported at all and every test touching config died at collection. This broke all four matrix legs. Pre-existing: the empty entries are unchanged from 27a3133. A plain mode="before" validator does NOT fix it - the error is raised upstream at the source. Fixed by annotating the fields NoDecode so the raw string reaches the validator, which accepts JSON arrays, comma-separated values, and blank (meaning no keys).
* Tests: `tests/test_config_consistency.py`
* Files: `shorts_engine/config/settings.py`
* Commits: `1c8a41d7dfa0838abbc6659302828ea24fc009d7`

### R4-20 — Package module shadowed by a same-named singleton `[DONE-VERIFIED]`

* Evidence: hotfolder_watcher.py defines a module-level singleton on line 219 (hotfolder_watcher = HotfolderWatcher()) and services/ingestion/__init__.py re-exports it under the same name as the submodule. That rebinds the package attribute to the INSTANCE, so patching the dotted path shorts_engine.services.ingestion.hotfolder_watcher.is_file_locked walks onto the instance and raises AttributeError. It happened to resolve correctly on Python 3.11 and not on 3.10 - the tests were relying on mock internals and import order, never on documented behaviour. Fixed by patching the module object obtained via importlib.import_module. Audited the whole package: this is the only module shadowed this way.
* Tests: `tests/test_local_ingestion.py`
* Files: `tests/test_local_ingestion.py`
* Commits: `6651c3fed3cf1021cc2a29e2c68d03b934fde5ea`

### R4-21 — tomllib is 3.11+, breaking collection on the Python 3.10 legs `[DONE-VERIFIED]`

* Evidence: tests/test_packaging_dependencies.py imported tomllib unconditionally. tomllib is stdlib from 3.11, so on the 3.10 matrix legs the module could not be imported and the whole file failed to collect with exit code 2. That file already imported tomllib before this session, so the three pre-existing tests in it were failing on 3.10 as well. Fixed with a tomli fallback plus tomli in requirements-dev.txt behind a python_version < "3.11" marker, and a skip if neither is present. Verified all three paths: 3.11 runs, tomli fallback runs, neither skips. Note: pyproject.toml declares requires-python >= 3.11, so the 3.10 legs test an unsupported interpreter. Keeping or dropping them is the owner's call; they now pass either way.
* Tests: `tests/test_packaging_dependencies.py`
* Files: `tests/test_packaging_dependencies.py`, `requirements-dev.txt`
* Commits: `842d7356c806c43d3fd90d804ddeca266f2ac8cb`

### R4-22 — Timing-dependent concurrency test made deterministic `[DONE-VERIFIED]`

* Evidence: test_postgres_render_concurrency_and_transcribe_overlap started worker 1, slept 0.05s, then started worker 2, and assumed worker 1 had claimed the render job meanwhile. On slower runners that race is lost, no render event is recorded, and the test dies with KeyError render_vid_01_start instead of reporting anything about concurrency. Observed on Windows CI. Replaced the fixed sleep with a threading.Event signalled from inside the render callable. The overlap being asserted is unchanged; it is now established by ordering rather than timing. Ran 10 consecutive times: 10 passed.
* Tests: `tests/test_standalone_worker_and_fanout.py`
* Files: `tests/test_standalone_worker_and_fanout.py`
* Commits: `d6443061b534bc22ed84ccea573e5794c33ce1b0`

### R4-23 — CI reporting step crashed on Windows (Git Bash DLL init) `[DONE-VERIFIED]`

* Evidence: The Windows Py3.10 leg PASSED its tests and then failed in the separate publish-summary step with exit code -1073741502 (0xC0000142, STATUS_DLL_INIT_FAILED) - an intermittent Git Bash startup failure on the Windows runner images, not a problem with the tests or the reporting. A separate step costs a second bash launch, so the reporting was folded into the step that already runs pytest, exiting with the value of PIPESTATUS[0] so pytest's exit code is preserved (verified: a failing command still exits 3, not 0). Separately required: shell bash on both steps, since pipefail and tee are not PowerShell and windows-latest defaults to it. Without that, both Windows legs failed instantly with exit code 1 and collected no results. Both are now guarded by test_posix_run_steps_pin_bash_shell, which fails if a POSIX-syntax step forgets the pin (verified by removing one).
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/ci.yml`
* Commits: `28f13f8ab81ee648f96143b9066b3e9a6914330d`, `90d585cb417b82f62452538acdd309573591c9c0`

### R4-24 — All CI workflows green `[DONE-VERIFIED]`

* Evidence: Every workflow and job passes on the branch head. Agent Gates (R3): Pytest, Ruff Lint, mypy Ratchet, Ledger Consistency, Dependency Audit, Sabotage Checks, Scale (1k), E2E Scenarios - 8/8. Industrial CI/CD and Reliability Gate: Security CVE Scan, Code Quality, Docker Build, Test Suite Matrix x4 (ubuntu and windows x 3.10 and 3.11) - 7/7. CI/CD Pipeline: Lint and Test Suite - pass. Locally, with a complete dependency install including ffmpeg and ffprobe, pytest -q -m "not network" gives 1107 passed, 23 skipped, 1 deselected, exit 0. Honest caveat: 23 tests are skipped, not executed - 18 browser-backed E2E (no Playwright browser installed here) plus 5 platform and other. Green means nothing failed, not that everything ran.
* Tests: `tests/test_fresh_eyes.py`
* Files: `.github/workflows/agent.yml`, `.github/workflows/ci.yml`, `.github/workflows/ci-cd.yml`
* Commits: `f97c180790a48a105628889cbec0bba062bf7d94`

### R4-25 — Intermittent matrix-leg failure, and the blind spot that hid it `[DONE-VERIFIED]`

* Evidence: Two matrix legs failed non-reproducibly, each in a way that produced no test-level information: 23f5d97 -> Test Suite Matrix (windows-latest - Py3.10): failure 5b9754e -> Test Suite Matrix (ubuntu-latest - Py3.10): failure Crucially, ubuntu Py3.10 SUCCEEDED on 23f5d97 and failed on 5b9754e, while windows Py3.10 did the opposite. Different legs, different commits, no overlap - the signature of an intermittent failure, not of either commit. Neither commit touches product or test code (one is a ledger and generated docs change, the other a .gitignore rule). Why it was undiagnosable: CI-4's reporting only annotates lines matching FAILED or ERROR. When pytest exits non-zero WITHOUT reaching per-test reporting - a crash, a teardown kill, an interrupted run - nothing is emitted and the job reads as a bare "Process completed with exit code 1". Runner logs are not readable from a restricted environment (the logs endpoint redirects to a blocked blob host), so the failure had no channel. CI-13 closes the blind spot: on a non-zero exit with no FAILED or ERROR line, it annotates the raw tail of the pytest output. Verified in isolation across three cases (normal failure, crash with no FAILED line, and success). Current state: on 7d433f7 (CI-13) ubuntu Py3.10 passed, confirming the failure is intermittent rather than caused by 5b9754e. The root cause is still unknown. On that same run the windows Py3.11 leg was CANCELLED by GitHub (infrastructure, not a test outcome), so it is unverified for that head; the available token cannot re-run jobs or dispatch workflows (403). ROOT CAUSE FOUND (CI-14). Exactly one test is marked `network`: test_pipeline_runner_processes_local_ingested_video. It calls PipelineRunner.run_pipeline_for_video, which initialises TranscriptionService and DOWNLOADS A FASTER-WHISPER MODEL FROM THE HUGGINGFACE HUB AT TEST TIME. Reproduced locally: the run log shows "Initializing TranscriptionService (model=base, device=cpu, compute_type=int8)" followed by "Failed to load faster-whisper model: ConnectError: TLS/SSL connection has been closed" - it reaches for the network, so its outcome tracks a third party's availability and rate limiting rather than the code under test. The correlation is what identified it, and it is clean: agent.yml uses -m "not network" -> never flaked, across every run ci.yml no filter -> failed twice ci-cd.yml no filter -> same exposure, now closed The failures moved between legs and operating systems between runs, and neither commit touched product or test code - the signature of an external dependency, not a regression. Fixed by adding -m "not network" to ci.yml and ci-cd.yml to match agent.yml, and by quarantining rather than dropping the network tests: a new `network-tests` job runs -m network nightly and on manual dispatch. It is a real gate that fails the workflow when it breaks - it simply runs on a cadence where a third-party outage is distinguishable from a regression.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/ci.yml`, `.github/workflows/ci-cd.yml`
* Commits: `7d433f757a97b491eb970a1f34f432c57827cb2d`, `c714e8e27c1527ed1079449a06f815d53a8abec3`

### R4-26 — Completion scorecard and externally-verified CI evidence `[DONE-VERIFIED]`

* Evidence: LEDGER FACT versus CI FACT. This row is the only place the two are mixed, so they are labelled explicitly. LEDGER FACT = a status recorded in docs/agent/ledger.yaml and reproduced by tools/gen_agent_docs.py. The generator does not run any gate; it only reflects what is written here. CI FACT = a workflow run observed through the GitHub Actions API, cited below with its run id, branch and commit sha so it can be re-checked. Nothing below is inferred. Where a gate did not run, it says so. CI FACTS - all three workflows, branch arena/c08e735f-yt-automation-clipping, commit dbaa05951cea977d422260dbf6148d8e7572c58a: Agent Gates (R3), run 39201025435, pull_request, success. 8/8 jobs: Pytest, Ruff Lint, mypy Ratchet, Ledger Consistency, Dependency Audit, Sabotage Checks, Scale (1k), E2E Scenarios. Industrial CI/CD and Reliability Gate, run 39201025438, pull_request, success. Jobs: Code Quality and Linting, Docker Container Build Verification, Security and CVE Vulnerability Scan, and Test Suite Matrix x4 (ubuntu-latest and windows-latest on Python 3.11). The Network Tests job reported SKIPPED on a push or pull_request event, which is correct - it is gated to schedule and dispatch. CI/CD Pipeline, run 39201025450, pull_request, success. Job: Lint and Test Suite. Deploy to Staging and Deploy to Production skipped, as expected for a branch run. LEDGER FACTS at dbaa059: 180 rows, tools/check_agent_ledger.py returns OK. ruff clean, mypy ratchet 0 <= baseline 0, git diff --check clean. LOCAL TEST RUN - not a substitute for CI. In this sandbox, at commit dbaa059, with ffmpeg and ffprobe present: pytest -q -m "not network" -> 1119 passed, 23 skipped, 1 deselected. Caveat carried forward: 23 tests are SKIPPED, not executed - 18 browser-backed E2E plus 5 platform/other. Green means nothing failed, not that everything ran. The environment was destroyed twice by sandbox resets and rebuilt from requirements.txt and requirements-dev.txt. NOT RUN - the network-marked test. The `network-tests` job has never executed: zero schedule-triggered runs exist for the workflow, and workflow_dispatch is refused with HTTP 403 because the available token is a GitHub App installation token without the workflows scope. See R4-27 for the exact manual steps. NINE-ROW COMPLETION SCORECARD (reconciles the earlier "8 of 9"): 1 Branch clean except documented artifacts - DONE-VERIFIED. 2 CI runs with actual successful results - DONE-VERIFIED, the three run ids above. Two earlier red commits are disclosed in R4-24 and R4-25: one genuine (a stale generated manifest) and one infrastructure-only. 3 ruff and dependency audit fail closed - DONE-VERIFIED. 4 Ledger validation strict, generated docs reproducible - DONE-VERIFIED, consecutive generator runs are md5-identical. 5 Stable collection with fresh reproducible results - DONE-VERIFIED. 6 Windows acceptance procedure safe and honestly labelled - BLOCKED. Corrected in place at docs/WINDOWS_ACCEPTANCE.md but never executed; it requires a disposable Windows VM. Nothing was run. 7 No secrets or protected artifacts staged - DONE-VERIFIED. 8 git diff --check clean and an accurate report - DONE-VERIFIED. 9 main untouched and PR 6 unmerged - DONE-VERIFIED. Net: 8 of 9 DONE-VERIFIED, 1 BLOCKED. No criterion is claimed on the basis of a review, a static check, or a run on a different platform.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `docs/agent/ledger.yaml`, `.github/workflows/ci.yml`
* Commits: `dbaa05951cea977d422260dbf6148d8e7572c58a`

### R4-27 — network-tests job cannot be dispatched from this environment `[DEFERRED]`

* Evidence: The `network-tests` job was quarantined behind `if: github.event_name == "schedule" || github.event_name == "workflow_dispatch"` so that third-party outages stop red-denying pull requests. The consequence is that it does not run on push, so it has produced no signal at all and its pass state is UNKNOWN, not passing. Two approaches were tried to execute it and both failed: 1. `gh workflow run "Industrial CI/CD & Reliability Gate" --ref arena/c08e735f-yt-automation-clipping` returns HTTP 403: Resource not accessible by integration. 2. `gh api -X POST repos/zainmustafam977/YT-AUTOMATION-Clipping/actions/ workflows/380432637/dispatches -f ref=...` returns the same 403. Root cause: the available token is a GitHub App installation token without the `workflows` scope. This is an environment limitation, not a repository or workflow defect. A third approach was not attempted, since the user caps each problem at three. Verified state: the workflow has ZERO schedule-triggered runs in its entire history. Its cron is `17 3 * * *` UTC. MANUAL STEPS to close this: 1. Wait for the next scheduled fire at 03:17 UTC, or 2. Run it yourself from the Actions tab: open "Industrial CI/CD & Reliability Gate", click Run workflow, select branch arena/c08e735f-yt-automation-clipping. A user session has the scope this token lacks. Do not mark this DONE-VERIFIED until the job has actually completed and its conclusion has been read back. A URL that renders is not evidence that a run succeeded.
* Tests: `tests/test_ci_workflow_hardening.py`
* Files: `.github/workflows/ci.yml`, `tests/test_ci_workflow_hardening.py`
* Commits: `dbaa05951cea977d422260dbf6148d8e7572c58a`

