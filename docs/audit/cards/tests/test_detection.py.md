# Assessment Cards: tests/test_detection.py

---

## Unit 1: test_detection Test Suite
| Field | Content |
|---|---|
| Location | `tests/test_detection.py:1-114` |
| What it does | Tests for the Detection Service: RSS poller, WebSub HMAC verification,
and new-video idempotency. Tests: `test_rss_poller_parses_new_video`, `test_rss_poller_skips_known_video`, `test_websub_hmac_validation_passes`, `test_websub_hmac_validation_rejects_tampered`. Fixtures/helpers: _make_atom_feed, _make_in_memory_db. |
| Industrial-grade? | **A** - High coverage test suite using pytest, isolated fixtures, mocking external I/O and asserting deterministic state transitions. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (fast execution without hanging). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (temporary directories, database rollbacks). **Concurrency safety:** Pass. **Atomicity:** Pass. **Secret hygiene:** Pass. **Logging:** Pass. **Telemetry:** N/A. **Testability:** Pass (test itself). **Cost:** Free (mocked external APIs). **Modularity:** Pass. **Performance:** Pass. |
| Improvement proposals / Alternatives | Ensure execution in CI with test matrix across both SQLite and PostgreSQL backends. |
