# Industry-Grade Audit — YouTube Shorts Automation Engine

**Audit date:** 2026-11-11
**Audited commit:** `e7b18820450ae49eacd6233a2d6ebec9bb7d26aa` (branch `freebuff/6ecfd064db983a192a38a99e`, PR #8)
**Auditor:** Buffy (automated + manual review)
**Scope:** Entire repository — Python backend (`shorts_engine/`), React SPA frontend (`frontend/`), CI/CD, Docker, migrations, tests, docs, repo hygiene.
**Methodology:** Static analysis (ruff, mypy, bandit, pip-audit), dependency review, secret/credential hygiene, auth/authorization review, SSRF/injection review, test & coverage review, CI/CD review, documentation-vs-code reconciliation, repo hygiene.

> This report **supersedes the narrative claims** in `README.md`/`ARCHITECTURE.md` where they conflict with the code. Every finding cites a file:line or a reproducible command. Severity: **P0** (block release) / **P1** (fix this quarter) / **P2** (backlog).

---

## 1. Executive Summary

| Dimension | Score | Verdict |
|---|---|---|
| Test suite health | 8/10 | 1,163 collected; full gate green on Linux/Windows at HEAD |
| Static analysis (ruff E/F) | 9/10 | Clean under configured rules |
| Type safety (mypy) | 3/10 | 1,051 errors; ratchet allows no regression but does not reduce debt |
| Security posture | 5/10 | Auth/CSRF/WebSub fail-closed fixed; defaults, SSRF, CVEs remain |
| Dependency hygiene | 4/10 | Spec drift between `pyproject.toml` and `requirements.txt`; starlette CVEs |
| Data layer | 6/10 | Migrations fragmented; SQLite/Postgres dual; no integrity check on ingest |
| CI/CD | 7/10 | Broad matrix; security job neutralized (`|| true`); no secret scan |
| Documentation | 4/10 | 51% claim-error rate per prior audit; README counts stale |
| Repo hygiene | 5/10 | Large binary tracked; runtime state committed; no LICENSE |
| Overall | **5.7/10** | Ship-ready for *internal* use only after P0 items |

**Bottom line:** The pipeline is functionally green and the recent hardening work (auth guards, CSRF, WebSub fail-closed, dry-run, host allowlist) materially improved security. What remains is **debt, not breakage**: type safety, dependency spec drift, SSRF gaps on outbound clients, missing license, and a large backlog of unverified/absent features advertised as delivered.

---

## 2. Repository Snapshot

| Metric | Value |
|---|---|
| Tracked files | 825 |
| Python source LOC (`shorts_engine/`) | 49,715 |
| Test LOC (`tests/`) | 33,546 |
| Frontend LOC (`frontend/src/`) | 5,508 |
| Tests collected | 1,163 (18 E2E) |
| Git commits on branch | 127 |
| Python runtime (venv) | 3.12.3 (project declares ≥3.11) |
| DB engines supported | SQLite (default) + PostgreSQL |

---

## 3. Static Analysis & Type Safety

### 3.1 Ruff
- Configured scope: `shorts_engine/` only (`tests/`, `scripts/`, `tools/`, `docs/` excluded). Rules: `E`, `F` (E501 ignored). **Passes clean.**
- **Finding (P2):** Excluding `tests/` and `tools/` from linting hides 33,546 LOC of test code from style checks. Recommend extending ruff to `tests/` with a relaxed rule set.
- Broader rule scan (`UP`, `B`, `S`, `SIM`, `PTH`, `PL`, `RUF`) surfaces ~3,000 style/modernization findings — dominated by `UP045`/`UP006` (typing modernization) and `PLC0415` (in-function imports). These are not bugs but indicate Python 3.9-era typing idioms.

### 3.2 Mypy
- Baseline: 1,070 errors. Current: **1,051 errors in 84 files** (ratchet allows no increase).
- Root cause: SQLAlchemy `Column[T]` is not narrowed to `T` at runtime; route handlers pass `Column[str]` where `str` is expected. This is a pervasive, mechanical problem (~60% of errors).
- **Recommendation (P1):** Introduce a thin mapping layer / `Mapped[]` annotations (SQLAlchemy 2.0 style) or use `scalar_one()`/attribute access patterns, then ratchet downward in chunks. Do not disable the ratchet.

### 3.3 Bandit
- **204 findings:** 146 `B110` (try/except/pass), 12 `B311` (random), 10 `B310` (urlopen), 7 `B603` (subprocess), 7 `B105` (hardcoded password strings — mostly false positives/status constants), 5 `B608` (string-built SQL — internal table names, low risk), 4 `B404`, 3 `B324` (hashlib/MD5), 2 each of `B607`/`B606`/`B405`/`B314`, 1 `B112`.
- **P1:** Audit the 146 `try/except/pass` blocks; many silently swallow errors in ingestion/rendering paths. Replace with logged, scoped handlers.
- **P1:** `B310` raw `urllib.request.urlopen` in `broll/client.py`, `rss_poller.py`, `alerter.py`, `oauth_manager.py` — no SSRF guard (see §5.4).

---

## 4. Security Posture

### 4.1 Authentication & Authorization — Improved
- Multi-user RBAC with PBKDF2-HMAC-SHA256 (600k iterations) + constant-time compare. ✅
- Session tokens HMAC-SHA256 signed, 7-day max age. ✅
- CSRF wired on batch, overlay, publish mutation routes. ✅ (AUD-008 fixed)
- Overlay mutation endpoints now require `require_operator` + `verify_csrf`. ✅ (AUD-005 fixed)

### 4.2 Secrets & Defaults — Open
- **P0 (AUD-006):** `DASHBOARD_PASSWORD` defaults to `"AdminPass12345!"` and `ensure_default_admin()` recreates the admin at boot. Production guard validates length ≥12 only; the default passes. → Make env-mandatory in production and refuse startup when the default is present.
- **P0:** `MASTER_ENCRYPTION_KEY`/`SECRETS_MASTER_KEY` are `Optional` and `ENFORCE_SECRET_ENTROPY` defaults to `False`, so placeholder keys only log a warning. → Default `ENFORCE_SECRET_ENTROPY=True` when `ENV in (staging, production)`.
- **P1:** `.env.example` ships usable weak defaults (master key, salt, admin password). → Replace with placeholders requiring generation; document `secrets.token_urlsafe(32)`.
- **P1:** No `LICENSE` file; README references a license section. → Add an explicit license.

### 4.3 WebSub / Ingestion — Fixed
- `WebSubVerifier.verify_signature()` is now fail-closed when `WEBSUB_SECRET` is unset. ✅ (AUD-007 fixed)

### 4.4 SSRF — Partially Open (P1)
- `url_guard` exists (`shorts_engine/utils/url_guard.py`, `services/security/url_guard.py`) and is applied to: webhooks, downloader, status.
- **Not guarded:** `broll/client.py` (urlopen to a download URL), `rss_poller.py`, `alerter.py`, `oauth_manager.py`. All fetch remote URLs derived from external input or config. → Apply `is_safe_url()` + DNS rebinding protection to every outbound HTTP call.

### 4.5 Injection
- No `shell=True`, no `os.system`, no `eval`/`exec`. ✅
- Subprocess calls use argument lists. ✅
- SQL uses SQLAlchemy ORM/`text()` with bound parameters in application code; string-built SQL exists only in internal migration/search paths using trusted table names. Low risk but should quote identifiers.

### 4.6 Host Header
- `DASHBOARD_ALLOWED_HOSTS` is now configurable (supports reverse-proxy domains). ✅ (AUD-019 fixed)

### 4.7 Crypto
- AES-256-GCM for vault; PBKDF2 600k iterations for passwords. ✅
- 3 `B324` (hashlib) findings — likely MD5 for cache keys/checksums; verify none are used for security-sensitive hashing.

---

## 5. Dependencies & Supply Chain

### 5.1 Spec Drift (P0)
- `pyproject.toml`: `fastapi>=0.100.0`, `uvicorn>=0.20.0`, `starlette` (transitive).
- `requirements.txt`: `fastapi>=0.110.0,<1.0.0`, `uvicorn[standard]>=0.28.0,<1.0.0`, `starlette>=0.36.0,<1.0.0`.
- Installed (venv): fastapi 0.162.2, starlette 1.10.0, uvicorn (latest) — **violates the `<1.0.0` caps**. CI installs via `requirements.txt` (capped) on one path and `pip install -e .[dev]` (uncapped) on another. → Reconcile: either lift caps to `>=1.x` and test, or pin consistently. Current `pip-audit` against `requirements.txt` reports **10 CVEs in starlette 0.52.1** (PYSEC-2026-161/248/249/2280/2281) with fixes requiring `>=1.0.1`, which the cap forbids. (AUD-009)

### 5.2 Other CVEs
- Installed env: 5 CVEs in `setuptools 78.1.0` (fix `>=84.1.0`). Low severity; upgrade.
- `torch`/`shorts-engine` not on PyPI; unauditable. Document the source (PyTorch CPU wheel index) and pin a hash.

### 5.3 Supply Chain
- Dockerfile pulls `ghcr.io/astral-sh/uv:latest` (mutable tag) and `python:3.11-slim` (mutable). → Pin by digest.
- `tools/bin/gitleaks.exe` (7.6 MB Windows binary) committed. → Remove; use the `gitleaks/gitleaks-action` in CI or install via package manager.
- `assets/fonts/seguiemj.ttf` (12.9 MB) committed. Consider Git LFS or a download step.

---

## 6. Data Layer & Migrations

- **Dual engine support** (SQLite default, Postgres optional) with a shared SQLAlchemy model layer. ✅
- **Migration fragmentation (P1, AUD-018):** registry versions (`002_channel_upload_defaults`, `003_source_video_overrides`) do not match files (`002_m3_postgres_scale`, `003_phase1_publishing_studio`); two distinct `005` suites; no `001`/`004`. → Reconcile registry ↔ files; add a single ordered migration manifest.
- **Ingestion (P2, AUD-017):** corrupt media (random bytes renamed `.mp4`) is accepted as `SourceVideo(DETECTED, duration 0)`. → Probe container integrity (ffprobe) before accepting.
- **SQLite concurrency:** WAL + 30s busy timeout verified. Heavy-writer contention untested. (AUD-029, UNVERIFIED)
- **Disk-full / housekeeping:** untested. (AUD-030, UNVERIFIED)

---

## 7. Testing & Quality Gates

- **1,163 tests collected** (18 E2E). Full gate green on Linux + Windows at HEAD.
- **Coverage:** `pytest --cov` available but no coverage threshold enforced in CI. → Add `--cov-fail-under=70` (or current baseline) to prevent regression.
- **Skipped/conditional tests:** OAuth tests skip when `storage/client_secret.enc` absent (now with actionable reason). Playwright tests skip without `playwright`. → Document in CI how to run them; provide a stub fixture.
- **Date-bomb test** fixed (publish worker slot allocation uses relative date). ✅ (AUD-014 fixed)
- **Sabotage/idempotency gaps (P1, AUD-011/012/013):** several idempotency controls are untested (tag removal, enqueue dedupe, crash-reconcile). Mutations survive undetected. → Add targeted sabotage tests.
- **Browser E2E** now wired into CI via `e2e-browser` job using `playwright install --with-deps`. ✅

---

## 8. CI/CD

- **Three workflows:** `agent.yml` (gates), `ci.yml` (industrial), `ci-cd.yml`. All green at HEAD.
- **Security job neutralized (P1, AUD-009):** `pip-audit -r requirements.txt || true` ignores failures. → Remove `|| true`; fail the job on CVEs above threshold.
- **No SAST/secret scan in CI:** bandit and gitleaks not run. → Add `bandit -r shorts_engine` (medium+) and gitleaks-action.
- **Windows matrix flakiness fixed:** pytest now runs under `bash` (PowerShell `ResourceUnavailable` 0xE9). ✅
- **Branch protection:** now enabled on `main` (user action completed). Required checks must be kept in sync as new gates are added.

---

## 9. Documentation & Governance

- **Docs-error rate ~51%** per prior audit (AUD-026): inflated counts, nonexistent evidence files, wrong paths. README claims "337+ tests" (actual 1,163) and "Python 3.10/3.11" (project requires ≥3.11).
- **No license file.**
- **Missing runbooks:** `RUNBOOK.md`, `THREAT_MODEL.md`, `ROADMAP.md`, ADRs referenced by ledger but absent (AUD-001).
- **Ledger consistency:** `tools/check_agent_ledger.py` runs in CI; 210 broken refs reported previously. Verify current state before relying on ledger claims.

---

## 10. Frontend (React 19 + Vite 6)

- Minimal SPA (5,508 LOC): Queue, Channels, Status, Logs, Settings pages; single `PublishingStudioPanel` component.
- No frontend tests (no vitest/jest). No ESLint dependency installed despite `lint` script. → Add at least smoke tests; fix `lint` script or remove it.
- `tsc -b` type-check in build; no runtime error boundary visible in audit. → Add error boundary + loading states.
- Batch operations have no UI surface (AUD-015).

---

## 11. Repo Hygiene

- **Tracked runtime state (P1, AUD-028):** `storage/detailed_failure_audit.json` (145 KB, real video data), `storage/quota_state.json` committed. → Remove from git; add to `.gitignore`.
- **Tracked binary (P1):** `tools/bin/gitleaks.exe`. → Remove; use CI action.
- **Large binary asset:** `assets/fonts/seguiemj.ttf` (12.9 MB). → LFS or download step.
- **Historical bloat:** `frontend/node_modules/`, `storage/*.db`, logs exist in git history (purged from HEAD). → `git filter-repo` if repo size matters (destructive; coordinate).
- **No `.env.local` tracked** (good). No secret-like strings in tracked files (single test fixture). ✅

---

## 12. Findings Register (Prioritized)

### P0 — Block release
| ID | Area | Title | Status |
|---|---|---|---|
| P0-1 (AUD-006) | security | Default admin password accepted in production | Open |
| P0-2 | security | `ENFORCE_SECRET_ENTROPY` defaults False (placeholder keys only warn) | Open |
| P0-3 (AUD-009) | security/deps | starlette CVEs via requirements.txt caps | Open |
| P0-4 | hygiene | No LICENSE file | Open |
| P0-5 | data | Corrupt media accepted at ingestion (no container probe) | Open (AUD-017) |

### P1 — This quarter
| ID | Area | Title | Status |
|---|---|---|---|
| P1-1 | quality | Mypy debt 1,051 errors; ratchet only prevents regression | Open (AUD-004) |
| P1-2 | security | SSRF guard missing on broll/rss/alerter/oauth outbound calls | Open (AUD-024) |
| P1-3 | security | 146 `try/except/pass` blocks (bandit B110) | Open (AUD-034) |
| P1-4 | ops | CI security job neutralized (`|| true`) | Open (AUD-009) |
| P1-5 | hygiene | Tracked runtime state + gitleaks binary | Open (AUD-028) |
| P1-6 | data | Migration fragmentation (registry vs files) | Open (AUD-018) |
| P1-7 | quality | Idempotency controls untested (sabotage gaps) | Open (AUD-011/12/13) |
| P1-8 | quality | Batch preview reports OK for items that fail at run | Open (AUD-016) |
| P1-9 | docs | Docs-error rate 51%; stale counts/paths | Open (AUD-026) |
| P1-10 | frontend | No frontend tests; broken `lint` script | Open |

### P2 — Backlog
| ID | Area | Title |
|---|---|---|
| P2-1 | quality | Ruff excludes tests/tools from linting |
| P2-2 | quality | No coverage threshold in CI |
| P2-3 | quality | `datetime.utcnow()` deprecated (140 sites) |
| P2-4 | quality | Unverified features advertised (F-12 SRT/VTT, F-17 rights ledger, F-23/24) |
| P2-5 | ops | Dead/unwired features (webhooks routes, telegram_notify, cli/ergonomics) |
| P2-6 | ops | Batch UI/CLI absent (API only) |
| P2-7 | data | SQLite lock contention / disk-full untested |
| P2-8 | frontend | No error boundary; batch UI |
| P2-9 | supply | Mutable Docker base-image tags |
| P2-10 | supply | Large binary assets (font, gitleaks) |

### Fixed in this audit cycle (verified at HEAD)
| ID | Title |
|---|---|
| AUD-002 | Full gate red → green (1,129+ passed) |
| AUD-003 | Ruff gate red → clean (E/F) |
| AUD-005 | Unauthenticated overlay mutations → guarded |
| AUD-007 | WebSub HMAC fail-open → fail-closed |
| AUD-008 | Batch CSRF missing → wired |
| AUD-010 | Dry-run publish broken → fixed |
| AUD-014 | Date-bomb DST test → fixed |
| AUD-019 | Host-header allowlist hardcoded → configurable |
| AUD-035 | OAuth tests hard-fail → skip with reason |
| — | Browser E2E wired into CI with `--with-deps` |
| — | Hard-coded Windows artifact paths removed |

---

## 13. Remediation Roadmap

**Week 1 (P0):** Remove default admin password + enforce entropy in prod; reconcile dependency caps and upgrade starlette; add LICENSE; add container probe at ingest. Run pip-audit without `|| true`.

**Weeks 2–4 (P1):** SSRF guard rollout; try/except/pass audit; migration manifest reconciliation; sabotage tests for idempotency; docs correction pass; remove tracked runtime state + gitleaks binary; add coverage threshold.

**Quarter (P2):** Mypy debt reduction in chunks; datetime modernization; frontend tests + error boundary; dead-code removal; Docker digest pinning; LFS for large assets.

---

## 14. Verification Commands

```bash
# Static
.venv/bin/ruff check shorts_engine
.venv/bin/mypy shorts_engine --ignore-missing-imports | tail -1
.venv/bin/bandit -r shorts_engine -q | grep -c 'Issue:'
.venv/bin/pip-audit -r requirements.txt

# Tests
.venv/bin/pytest -m "not network and not e2e" -q tests/
.venv/bin/pytest tests/e2e -q

# Hygiene
git ls-files | grep -E 'node_modules|\.db$|detailed_failure_audit|quota_state|gitleaks\.exe'
```

---

## 15. Appendix — Tool Versions

| Tool | Version |
|---|---|
| Python (venv) | 3.12.3 |
| ruff | 0.17.4 |
| mypy | 2.4.1 |
| bandit | 1.9.4 |
| pip-audit | (latest) |
| pytest | 8.x |

*Generated by Buffy. Findings reference `docs/audit/findings.yaml` for the prior audit baseline; this report reflects state at commit `e7b1882`.*