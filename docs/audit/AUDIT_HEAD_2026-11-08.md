# Independent Repository Audit — HEAD `82ecaa3`

**Role assumed:** independent release-gate auditor (application security + code quality + release engineering). Not the builder; no repo code was modified except this report.
**Date:** 2026-11-08
**Target:** `ZACODEC-IO/YT-AUTOMATION-Clipping`, branch `main`, HEAD `82ecaa3`
**Method:** fresh Python 3.12 venv built only from `requirements.txt` + `requirements-dev.txt`, full test-suite execution, runtime route/dependency enumeration against a live `create_dashboard_app()`, behavioural HTTP probes, static analysis (`ruff`, `mypy` with and without project config, `pip-audit`), and targeted source review of the security, publishing, persistence, and deployment boundaries.
**Prior audit:** `docs/audit/VERIFICATION_REPORT.md` (verdict NOT READY at `80370f5`). This report re-verifies at HEAD and adds new findings; several prior findings persist.

> **Remediation status (2026-11-08):** the P0 items F-01, F-02, F-03 and F-04 below have since been fixed in this working tree (see §7). The findings are retained as the record of the audited commit; F-05 to F-13 remain open.

---

## 1. VERDICT: **NOT PRODUCTION READY**

| Gate | Result at HEAD | Status |
|---|---|---|
| `ruff check .` (CI's own lint command) | **35 errors**, incl. 5 `F821` undefined names | ❌ RED |
| mypy gate | configured `ignore_errors = true`; real count **1058 errors / 85 files** | ❌ VACUOUS |
| `pytest tests -q` (clean Linux env) | **46 failed**, ~1036 passed, 5 errors, 1 skipped; 2 modules unimportable cold | ❌ RED |
| Full-run stability | the full-suite run **died with SIGSEGV** before completion | ❌ UNSTABLE |
| Authorization coverage | **37 of 202 routes unauthenticated; 9 mutating** | ❌ FAIL |
| Default credentials in production | shipped `AdminPass12345!` **accepted** with `ENV=production` | ❌ FAIL |
| Deployment behind bundled Caddy | every request with a real domain → **HTTP 400** | ❌ BLOCKER |
| Dependency audit gate | `pip-audit … || true`; 10 starlette advisories | ❌ NOT GATED |
| Secrets in tracked files | none found | ✅ PASS |
| `init_db()` from empty DB | 12 migrations apply cleanly | ✅ PASS |
| Non-root container, TLS headers, PBKDF2 auth, httponly signed session | present | ✅ PASS |

The engine is a substantial, genuinely functional system with unusually broad automated tests. The blocker is not "no engineering" — it is that **the release gates are reported green while objectively failing**, and that **three defects would be exploitable or fatal in a real deployment**.

---

## 2. VERIFIED BASELINE (independent measurements)

| Metric | Measured | Note |
|---|---|---|
| Python files / LOC (`shorts_engine` + tests) | 176 source modules, 49,133 LOC; 157 test files, 32,810 LOC | `wc -l`, `git ls-files` |
| Tests collected | **1,108** | `pytest --co` with torch installed |
| Test outcome (split run, `--continue-on-collection-errors`) | 1,036 passed, **46 failed**, 5 errors, 1 skipped | full logs retained during audit |
| Cold-import failure | `import shorts_engine.services.publishing.client` → `ImportError` | reproducible in a fresh interpreter |
| Real mypy violations | **1,058** in 85 files | `mypy --config-file=/dev/null` |
| Tracked files | 2,345, incl. **1,526 under `frontend/node_modules/`** | `git ls-files` |
| Commits in history | 97 | `git log` |
| Hardcoded credentials/keys | none detected in tracked files | regex sweep; `tests/test_secretbox.py` only test material |

`docs/audit/01_baseline.md` states "154 Python files, 29,876 lines, 266 passed / 0 failed, 0 flaky". At HEAD the tree is 2.4× larger and the suite is red. **The baseline document is stale and must not be used for release decisions.**

---

## 3. FINDINGS

Severity reflects impact if this code were deployed as-is. Each item was reproduced, not inferred.

### F-01 · CRITICAL · Authorization is applied per-route and is missing on 37 endpoints (9 mutating)

`shorts_engine/dashboard/app.py:170-192` includes routers with no global auth dependency; each route must opt in with `Depends(get_current_user)`. Runtime enumeration of the live app found **202 API routes, 37 unauthenticated, 12 mutating** (login/logout/websub are legitimately public). Confirmed by unauthenticated `TestClient` calls against a clean DB:

```
GET  /api/overlays/presets            -> 200   (unauth read)
POST /api/overlays/seed-builtins      -> 200   (unauth WRITE)
GET  /api/search?q=test               -> 200   (unauth global search)
GET  /api/saved-views                 -> 200   (unauth read)
GET  /api/status                      -> 401   (controlled)
GET  /api/channels                    -> 401   (controlled)
```

Unauthenticated **state-changing** routes:

| Route | File |
|---|---|
| `POST /api/overlays/presets`, `/import`, `/{name}/compile`, `/{name}/validate`, `/apply`, `/seed-builtins` | `dashboard/routes/overlays.py` (0 auth in 12 routes) |
| `POST/PUT/DELETE /api/saved-views…` | `dashboard/routes/saved_views.py` (0 auth in 5 routes) |

Unauthenticated **read** routes that expose operational data: `/api/search`, `/api/command-palette`, `/api/triage/needs-attention`, `/api/triage/by-kind/{kind}`, `/api/overlays/audit`, `/api/overlays/preview/{name}`, `/api/metrics/ai-tokens`, `/oauth2callback`.

`saved_views.py` also takes `owner` from the request body (default `"system"`) and has no user scoping at all, so there is no ownership model to enforce even if auth were added.

**Impact:** anyone who reaches the dashboard can mutate overlay presets/candidates and read searchable clip/transcript data. This is the same class of defect the prior audit raised; it has regressed to more endpoints, not fewer.

---

### F-02 · CRITICAL · Production accepts the source-shipped default admin password

`config/settings.py:477` defaults `DASHBOARD_PASSWORD="AdminPass12345!"`; `dashboard/auth.py:39` creates the `admin` user with it. The staging/production guard (`settings.py:586-592`) rejects *common* passwords and anything shorter than 12 chars — `AdminPass12345!` is 15 chars and not in the deny list, so it passes.

Reproduced with `ENV=production` and strong keys:

```
PRODUCTION SETTINGS ACCEPTED with password = 'AdminPass12345!' | ENV = production
```

**Impact:** a deploy that forgets to set `DASHBOARD_PASSWORD` exposes `/login` with published credentials `admin / AdminPass12345!` (also printed in `QUICK_START.md:76` and `setup_environment.py:238`). This is full admin takeover of a dashboard that can publish to YouTube.

**Fix:** add the shipped default to `insecure_passwords`, and require an explicit password whenever `ENV != "development"` (fail closed).

---

### F-03 · CRITICAL · The provided production reverse proxy makes the app return 400 for every request

`dashboard/app.py:128-141` hardcodes allowed hosts:

```python
allowed_hosts = {"127.0.0.1", "localhost", "testserver", "[::1]"}
```

`Caddyfile` forwards the real domain (`header_up Host {host}`), and `docker-compose.yml` runs Caddy in the `production` profile. Reproduced:

```
Host=localhost:8080      -> 200
Host=127.0.0.1:8000      -> 200
Host=shorts.example.com  -> 400 {"detail":"Invalid Host header ... Localhost access only."}
Host=my.domain.co.uk     -> 400
```

**Impact:** the documented production topology cannot serve a single request. Either deployments are silently bypassing the provided Caddy, or the "production" path has never been exercised.

**Fix:** drive allowed hosts from config (`ALLOWED_HOSTS`) with the deployment domain, and include the proxy's forwarded host semantics.

---

### F-04 · HIGH · WebSub HMAC verification fails open by default

`services/detection/websub.py:147-150`:

```python
if not self.secret:
    logger.debug("No WEBSUB_SECRET configured — skipping signature verification (dev mode).")
    return True
```

`WEBSUB_SECRET` defaults to `None` (`settings.py:467`), and `dashboard/routes/websub.py` is public by design. Reproduced:

```
secret=None verify_signature(unsigned body) -> True
secret set  verify_signature(unsigned body) -> False
```

**Impact:** with default config, anyone can POST a forged Atom notification and enqueue arbitrary `video_id`s for ingestion — unauthenticated work injection / resource exhaustion, and a path into the downloader.

**Fix:** refuse to start (or reject notifications) when `WEBSUB_SECRET` is unset outside development.

---

### F-05 · HIGH · Circular import makes the publishing client unimportable in a cold process

`services/publishing/client.py:12` imports `services/publishing/worker.py`; `worker.py:471` (module level) imports `orchestration.exceptions`, which executes `orchestration/__init__.py:40` → `orchestration/runner.py:36` → back into the partially initialised `client.py`.

Reproduced in a fresh interpreter:

```
$ python -c "import shorts_engine.services.publishing.client"
ImportError: cannot import name 'YouTubePublisher' from partially initialized module
'shorts_engine.services.publishing.client' (most likely due to a circular import)
```

**Impact:** two test modules (`test_acceptance_criteria.py`, `test_multi_platform_syndication.py`) cannot be collected unless some earlier module happened to preload `orchestration` — i.e. **the suite passes only because of alphabetical import ordering**. Any consumer importing the syndication client first (script, worker, plugin) crashes at import.

**Fix:** move the `orchestration.exceptions` import in `worker.py` to the top of the module (or make `orchestration/__init__.py` lazy) so the dependency graph is acyclic.

---

### F-06 · HIGH · Threaded syndication shares one SQLite connection; full run died with SIGSEGV

`services/publishing/manager.py:160-199` builds `sessionmaker(bind=self.db.get_bind())` and queries the DB inside a `ThreadPoolExecutor`. When the bind is an in-memory SQLite engine with `StaticPool` (the exact fixture used by `tests/test_multi_platform_syndication.py:23-30`, and common in this suite), **every thread receives the same DBAPI connection**.

Observed:

```
Fatal Python error: Segmentation fault
  File "sqlalchemy/orm/loading.py", line 231 in chunks
  File "shorts_engine/services/publishing/manager.py", line 174 in _publish_worker
  File "concurrent/futures/thread.py", line 58 in run
```

A direct stress of the same shape (one StaticPool engine, one `Session` per worker thread) reproduced connection-state corruption **3/3 runs**: `IndexError: tuple index out of range`.

**Impact:** process death in the publishing path under concurrency — the worst possible place for a crash (mid-syndication). It is intermittent, which is why the suite usually looks green.

**Fix:** give each worker thread its own engine/connection (or a `NullPool`/file-backed engine with WAL), and never share a `StaticPool` connection across threads. Add a multi-thread regression test with a file-backed SQLite DB.

---

### F-07 · HIGH · Five latent `NameError`s, two of them reachable

`ruff` `F821` findings, reviewed at their use sites:

| Location | Code | Reachability |
|---|---|---|
| `services/publishing/worker.py:141` | `get_default_client_config()` | **not defined anywhere in the repo** — `build_youtube_analytics_client()` raises `NameError` on every call without an explicit config |
| `dashboard/app.py:49` | `logging.getLogger(...)` in `lifespan` | `logging` is not imported (only a local import exists in a different block) → masks the original startup error with `NameError` |
| `db/session.py:262` | `datetime.utcnow()` | `datetime` not imported; wrapped in `try/except Exception: pass`, so the rejection-data backfill silently never runs |
| `db/data_migration.py:180` | `migrated_for_table += …` | variable initialised only in a later branch → `UnboundLocalError` when migrating `schema_migrations` |
| `services/execution/watchdog.py:247` | `Dict[str, Any]` | harmless at runtime (local annotation not evaluated); lint-only |

Two of these are silent data/feature failures rather than crashes because the exceptions are swallowed. That is why tests do not catch them.

---

### F-08 · HIGH · The advertised quality gates do not exist

1. **Ruff is red.** CI runs `ruff check .` (`.github/workflows/ci.yml:30`) and it fails with 35 errors at HEAD. `pyproject.toml` excludes `tests`, `tools`, `scripts`, `docs`, `frontend` — the lint gate inspects only part of the repo.
2. **mypy is vacuous.** `pyproject.toml:93` sets `ignore_errors = true`, so `tools/check_mypy_ratchet.py` always reports `OK: mypy errors 0 <= baseline 0`. Real count with the same scope: **1,058 errors in 85 files**. The `.mypy_baseline.txt` "0" is meaningless.
3. **Dependency audit cannot fail.** `.github/workflows/ci.yml:51` ends in `pip-audit -r requirements.txt || true`. Current result: **10 advisories in starlette 0.52.1**, fixable only at `starlette>=1.0.1/1.1.0/1.3.x`, while `requirements.txt` pins `starlette>=0.36.0,<1.0.0`.
4. **No coverage gate**, no mutation gate, no artefact/secret scan wired into CI (`.pre-commit-config.yaml` runs ruff + ledger + ratchet only).
5. **CD steps are echo-only** (`ci-cd.yml` staging "trigger healthcheck" prints a message), and `CD_ENABLED` defaults off.

---

### F-09 · MEDIUM · 46 test failures at HEAD in a clean environment

Deterministic on re-run (8.8 s for the failing set). Grouped by cause:

| Cause | Count | Examples |
|---|---|---|
| Missing `storage/client_secret.enc` (tests not hermetic; require a bootstrap step) | 13 | `test_channel_oauth_flow`, `test_phase1_oauth_identity_guard` |
| `ffmpeg` absent in this sandbox (environment, would pass in CI) | ~12 | `test_clip_production_fixes`, `test_rendering`, `test_phase3_caption_and_compositor` |
| Windows-only assumptions not skipped on Linux | 5 | `test_win32_job`, `test_watch_folder_daemon` ×2, `test_local_ingestion`, `ctypes.windll` |
| Platform-conditional font asserted unconditionally (`Segoe UI Emoji` vs `Noto Color Emoji`) | 2 | `test_phase3_caption_and_compositor` |
| **Date bomb** — asserts `date == 2026-10-15` | 1 | `test_publish_slots.py:133` (fails today; failed in the prior audit too) |
| **Stale contract** — worker now requires `slot_id`, tests send legacy payload | 5 | `test_publish_worker_and_oauth_isolation` (`PermanentError: Missing mandatory slot_id binding`) |
| **Stale contract** — `ALLOW_IN_PROCESS_PREVIEW` env gate not set by tests | 2 | `test_review_queue`, `test_w3_2_lifecycle` |
| **Stale mock** — patches `status.threading.Thread`, module has no `threading` | 1 | `test_enhancements_phase3` |
| Real behavioural assertions | 5 | health probes 503, `202 != 200` on approval, `200 != 409` on re-reject, `None is not None` ingestion, worker `execute_job` |

The suite is valuable but **not hermetic**: it depends on ambient files (`storage/client_secret.enc`), the host platform, the calendar date, and an undocumented env flag. CI advertises these as green (`FINAL_REPORT.md` claims "266 passed / 0 failed / 0 flaky").

---

### F-10 · MEDIUM · Stored XSS surface in server-rendered dashboards

No escaping helper exists in any template (`grep -c escapeHtml` → 0). Templates assemble HTML by string concatenation from API data and assign via `innerHTML`, e.g.:

* `templates/queue.html:3487` — `'<td … title="' + (job.title || '') + '">' + (job.title …)` — `title` originates from LLM/YouTube metadata.
* `templates/queue.html:2685` — advisory strings injected as `<li>…</li>`.
* `templates/status.html:1395-1448` — `stage_label`, `status`, `youtube_video_id`, counts interpolated into `innerHTML` and `onclick="…('…')"`.
* `templates/calendar.html:153` — `youtube_video_id` concatenated into an anchor.

**Impact:** a malicious video title, channel name, or LLM-generated hook becomes script execution in an authenticated operator's session (session cookie is `HttpOnly`, but CSRF token and API actions are reachable from the page).

**Fix:** escape all interpolated values (`textContent`/`createElement`), and add CSP (Caddy sets no CSP header).

---

### F-11 · MEDIUM · Repository hygiene / supply chain

* **1,526 `frontend/node_modules/**` files are tracked in git** (including `esbuild.exe`, `rollup.win32-x64-msvc.node`). Binary dependency blobs are committed rather than installed from the lockfile — unreviewable, platform-specific, and impossible to audit.
* **Proprietary font committed:** `assets/fonts/seguiemj.ttf` (Microsoft Segoe UI Emoji) — redistribution is not licensed; the repo already ships DejaVu (freely licensed) alongside it.
* `tools/bin/gitleaks.exe` — committed Windows binary with `LICENSE`/`README` but no checksum or provenance.
* Junk in the tree: `shorts_automation_m2_bundle.zip` (188 KB), `setup.ps1.bak`, `scratch_queue.html` (206 KB), `in progress dev/setup (2).log`, 5 `.docx` duplicates of Markdown specs.
* **Two parallel migration systems with colliding prefixes:** `migrations/` holds SQL pairs `003_*`, `005_*`, `006_*`, `007_*`, `008_*` while the runtime registry (`shorts_engine/db/migrations.py`) uses `001…011` with different names (`005_batch_engine_tables` and `005_phase1_publishing_studio` both exist). Drift between the two is a schema-integrity risk.
* `shorts_engine/services/abtesting/` is an empty duplicate of `ab_testing/`.
* Repository content is ~145 MB, of which `frontend/node_modules` is ~34 MB of committed dependency blobs.

---

### F-12 · MEDIUM · Documentation contradicts the code (release-decision risk)

| Claim | Reality |
|---|---|
| `README.md:181,310` "337+ tests", `docs/audit/01_baseline.md` "266 passed / 0 failed / 0 flaky" | 1,108 tests; 46 fail at HEAD |
| `FINAL_REPORT.md` "Ruff: clean", "mypy 0 errors", "0 PARTIAL/BLOCKED/DEFERRED" | ruff 35 errors; mypy 1,058 real errors; 46 failing tests |
| `README.md:205` and the badge: tokens "encrypted at rest using **AES-256-GCM**" | `db/security.py` standardises on **Fernet** (AES-128-CBC + HMAC-SHA256), with GCM only as a legacy-decrypt fallback |
| Observability claims a correlation ID on every log line | `correlation_id` occurs **0 times** in `shorts_engine/` |
| `docs/audit/01_baseline.md` inventory (154 files) | 176 source modules / 362 total `.py` |

The prior audit's evidence file `docs/audit/evidence/mypy_evidence.txt` already recorded the 1,040-error reality; the builder's ledger was never corrected.

---

### F-13 · LOW · Container and config ergonomics

* `Dockerfile` final `CMD ["python", "cli.py", "status"]` — `docker run <image>` prints a status report and exits; the server only works because compose overrides `command`. A published image would appear "broken".
* Production image installs `pytest` and the full dev toolchain (builder step installs `pytest` into `/opt/venv`).
* `CMD`/`WORKDIR` fine, runs as non-root `appuser` ✅.
* `ENFORCE_SECRET_ENTROPY` defaults to **False** (`settings.py:56`), so placeholder keys only warn outside staging/production; `SECRETS_MASTER_KEY` has no default and failures surface late.
* `pyproject.toml` metadata is wrong for a package: `readme = "CLARIFICATION_QUESTIONS.md"`, and its dependency list has drifted from `requirements.txt` (no `asyncpg`, `starlette`, `tzdata`; different numpy bounds), so `pip install .` installs a different environment than the documented one.

---

## 4. WHAT IS GENUINELY GOOD

Credit where due — this is not a shell:

* **Persistence & migrations:** `init_db()` applied 12 migrations cleanly from an empty database in every probe; WAL/foreign-key pragmas present; durable `pipeline_jobs` queue with attempt counts, backoff, DLQ and reaper.
* **Auth primitives:** PBKDF2-HMAC-SHA256 at 600k iterations, signed `user.ts.hmac` session tokens, `HttpOnly`/`SameSite=Lax`/`Secure`-in-prod cookie, CSRF token bound to the session with `compare_digest`, role dependencies (`require_admin`/`require_operator`) that genuinely work where applied (`/api/status`, `/api/channels` → 401).
* **Secret handling:** no hardcoded credentials in tracked files; Fernet `secretbox` + legacy decrypt path; production guard rejects placeholder `DASHBOARD_SECRET_KEY`/DB passwords.
* **SSRF/URL guarding, subprocess watchdog** with process-group isolation and timeouts; no `shell=True` anywhere in `shorts_engine/`.
* **Search SQL is safe** — table names come from an internal `SEARCH_TARGETS` constant, values are not interpolated.
* **Test breadth:** 1,036 passing tests across pipeline stages, quota accounting, OAuth isolation, backup/restore, batch scale, and sabotage checks that actually call routes without auth and assert rejection.
* **Container/reverse proxy basics:** non-root user, HSTS/nosniff/frame-deny/referrer headers in Caddy.

---

## 5. PRIORITISED REMEDIATION PLAN

**P0 — before any deployment (each is small):**
1. Add auth dependencies to `overlays.py` (all 12) and `saved_views.py` (all 5, scoped to `current_user`); add a test that asserts **every** non-allowlisted route returns 401 unauthenticated (the enumeration script in this audit is a starting point).
2. Add `"adminpass12345!"` to the deny list and make `ENV in (staging, production)` fail when `DASHBOARD_PASSWORD` is unset or equals the shipped default.
3. Replace the hardcoded `allowed_hosts` with configuration; add the deployment domain; add a compose/Caddy smoke test with a non-localhost `Host`.
4. Fail closed when `WEBSUB_SECRET` is unset.
5. Fix the `client.py ↔ orchestration` import cycle and add a cold-import test (`python -c "import shorts_engine.services.publishing.client"`).
6. Give each syndication thread its own engine/connection; add the multi-thread test that currently crashes.

**P1 — make the gates honest (1–2 days):**
7. Make `ruff check` pass (most are unused imports; the 5 `F821`s are real — fix `worker.py:141`, `app.py:49`, `session.py:262`, `data_migration.py:180`).
8. Remove `ignore_errors = true`, set the ratchet baseline to the true count (1,058) and ratchet down; wire `tools/check_mypy_ratchet.py` into CI.
9. Remove `|| true` from `pip-audit`; unpin starlette (`>=1.3.1`) and re-test.
10. Make the suite hermetic: generate a fake `client_secret.enc` fixture, `skipif` Windows-only tests, remove the date bomb, set `ALLOW_IN_PROCESS_PREVIEW` in the test env, update the `slot_id` payload contracts, fix the `status.threading` mock.
11. Delete committed `node_modules`, the proprietary `seguiemj.ttf`, `tools/bin/gitleaks.exe`, the zip/`.bak`/logs/docx duplicates; reconcile the two migration systems.

**P2 — hardening (ongoing):**
12. Escape all template interpolations; add CSP.
13. Make `ENFORCE_SECRET_ENTROPY` default true outside development.
14. Fix the Dockerfile `CMD`, drop dev deps from the runtime image, align `pyproject.toml` with `requirements.txt`.
15. Correct the stale audit/baseline/README claims so future release decisions rest on measured facts.

---

## 6. HOW THIS WAS VERIFIED (reproduction notes)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
# torch CPU wheel installed separately (CI dependency, not in the VM image)
.venv/bin/ruff check .                                  # 35 errors
.venv/bin/mypy --no-error-summary shorts_engine/        # 0 (ignore_errors=true)
.venv/bin/mypy --config-file=/dev/null --ignore-missing-imports shorts_engine/   # 1058 errors
.venv/bin/python tools/check_mypy_ratchet.py            # OK: 0 <= 0
.venv/bin/pip-audit -r requirements.txt                 # 10 starlette advisories
.venv/bin/python -m pytest -q                           # SIGSEGV in threaded syndication test
.venv/bin/python -m pytest -q --continue-on-collection-errors <split suite>     # 46 failed
.venv/bin/python -c "import shorts_engine.services.publishing.client"          # ImportError
```

Runtime probes (throwaway scripts, removed after use): route/dependency enumeration (202 routes / 37 unprotected), unauthenticated HTTP calls, `Host` header matrix, WebSub signature check, `ENV=production` settings validation, and a 4-thread StaticPool stress (3/3 corruption errors).

**Not verified in this environment:** real ffmpeg rendering, GPU/hwaccel paths, live YouTube/TikTok/Instagram APIs, Playwright E2E, PostgreSQL-mode behaviour, and actual container build (no Docker daemon). Those are marked environment-blocked, not passed.

---

## 7. P0 REMEDIATION APPLIED

| Finding | Change | Verification |
|---|---|---|
| **F-01** authorization | `routes/overlays.py`: router-level `Depends(require_viewer)`; all six mutating routes add `Depends(require_operator)` + `Depends(verify_csrf)`. `routes/saved_views.py`: router auth, owner derived from the session (`user.username`), every read/update/delete scoped to that owner, CSRF on writes. | Anonymous `GET /api/overlays/presets`, `POST /api/overlays/seed-builtins`, `GET/POST /api/saved-views` → **401**; cookie-only mutation → **403**; cross-user view access → **404**. Route enumeration: 20 unauthenticated routes remain, of which only `/login`, `/logout`, `/websub/callback` are mutating. |
| **F-02** default password | `config/settings.py`: shipped value extracted to `SHIPPED_DEFAULT_DASHBOARD_PASSWORD` and added to the staging/production deny list. `dashboard/auth.py`: `ensure_default_admin` refuses to bootstrap the admin with the shipped password outside development. | `PipelineSettings(ENV="production", DASHBOARD_PASSWORD="AdminPass12345!")` → `ValidationError: Insecure DASHBOARD_PASSWORD`. |
| **F-03** host allowlist | `config/settings.py`: new `DASHBOARD_ALLOWED_HOSTS` (comma-separated, `*` opt-out). `dashboard/app.py`: middleware reads the setting instead of a hardcoded localhost set. `docker-compose.yml` passes the allowlist plus `${DOMAIN}`; `.env.example` documents it. | With `DASHBOARD_ALLOWED_HOSTS=shorts.example.com,localhost`: domain → 200, `evil.example.com` → 400. |
| **F-04** WebSub | `services/detection/websub.py`: unsigned notifications are rejected when no secret is configured (fail-closed), replacing the silent dev bypass. | `WebSubNotificationParser(secret=None).verify_signature(...)` → `False`. |

Regression tests added: `tests/test_p0_security_hardening.py` (host allowlist + anonymous sweep), plus auth/CSRF/ownership cases in `tests/test_overlay_routes.py`, `tests/test_saved_views_routes.py`, `tests/test_detection.py`, and `tests/test_config_consistency.py`.

Full-suite comparison against the audited baseline: **46 failed / ~1044 passed before and after, with neither new failures nor changed failure identities**; `ruff` remains at its pre-existing 35 findings (no new ones introduced).
