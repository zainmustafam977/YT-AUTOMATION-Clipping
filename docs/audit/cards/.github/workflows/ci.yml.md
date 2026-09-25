# Assessment Cards: .github/workflows/ci.yml

---

## Unit 1: Industrial CI/CD & Reliability Gate Workflow
| Field | Content |
|---|---|
| Location | `.github/workflows/ci.yml:1-109` |
| What it does | GitHub Actions CI workflow triggered on push/PR to `main` and `develop`. Executes 4 jobs: 1) `lint-and-format` (Ruff linter), 2) `security-audit` (`pip-audit` for CVE detection), 3) `test-matrix` (cross-platform Linux/Windows with Python 3.10/3.11, installing FFmpeg via apt/choco, running `setup_environment.py`, and pytest), and 4) `docker-build` (container build verification). |
| Industrial-grade? | **B+** - Excellent cross-platform matrix testing including native FFmpeg on both Linux and Windows runners, but security audit ignores failures via `|| true`, and mypy is installed without running. |
| Checklist | **Error handling:** Needs Work (`pip-audit ... || true` swallows security vulnerabilities without blocking merges). **Timeouts:** Needs Work (no `timeout-minutes` declared on jobs, leaving them vulnerable to runner hangs up to default 6 hours). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (valid GitHub Actions YAML). **Resource cleanup:** Pass (ephemeral runners). **Concurrency safety:** Pass (can declare `concurrency` group to cancel obsolete PR runs). **Atomicity:** Pass. **Secret hygiene:** Pass (no secrets exposed; `push: false` in docker build). **Logging:** Pass. **Telemetry:** Pass (job summaries in GitHub Actions). **Testability:** Pass (exercised on every commit). **Cost:** Reasonable (matrix utilizes standard free-tier runners). **Modularity:** Pass. **Performance:** Good (uses pip caching via setup-python). |
| Improvement proposals / Alternatives | 1. **Add job timeouts**: Set `timeout-minutes: 15` on `test-matrix` and other jobs to prevent stalled runners from exhausting CI quota. 2. **Add concurrency cancel-in-progress**: Add `concurrency: group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true` to prevent queuing multiple redundant test runs on repeated pushes. 3. **Run mypy**: Either invoke `mypy shorts_engine` or remove mypy from the pip install command. 4. **Enforce CVE scanning**: Remove `|| true` from `pip-audit` or configure an explicit ignorelist for accepted advisories. |
