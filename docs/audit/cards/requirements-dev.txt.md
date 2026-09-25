# Assessment Cards: requirements-dev.txt

---

## Unit 1: Development & Testing Tooling Manifest
| Field | Content |
|---|---|
| Location | `requirements-dev.txt:1-20` |
| What it does | Specifies developer and testing tooling. Extends base `requirements.txt` with Pytest test runners (`pytest`, `pytest-asyncio`, `pytest-cov`), Playwright for headless browser visual verification, Ruff for linting/formatting, and Mypy with typing stubs (`types-psutil`, `types-pillow`). |
| Industrial-grade? | **A** - Follows Python best practices by cascading from base requirements and providing modern testing and static analysis packages. |
| Checklist | **Error handling:** N/A. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **Secret hygiene:** Pass. **Logging:** N/A. **Telemetry:** N/A. **Testability:** Pass (exercised in CI workflows). **Cost:** Free. **Modularity:** Pass. **Performance:** Pass. |
| Improvement proposals / Alternatives | None. |
