# Assessment Cards: tests/test_calendar_routes.py

---

## Unit 1: test_calendar_routes Test Suite
| Field | Content |
|---|---|
| Location | `tests/test_calendar_routes.py:1-150` |
| What it does | Unit and Integration Tests for Content Calendar Routes:
- Tests /calendar view rendering
- Tests /api/calendar/events JSON feed (scheduled vs published clips)
- Tests /api/calendar/reschedule/{clip_id} drag-and-drop endpoint Tests: `test_setup`, `test_calendar_html_view`, `test_calendar_events_feed`, `test_calendar_reschedule_endpoint`. Fixtures/helpers: override_get_db. |
| Industrial-grade? | **A** - High coverage test suite using pytest, isolated fixtures, mocking external I/O and asserting deterministic state transitions. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (fast execution without hanging). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (temporary directories, database rollbacks). **Concurrency safety:** Pass. **Atomicity:** Pass. **Secret hygiene:** Pass. **Logging:** Pass. **Telemetry:** N/A. **Testability:** Pass (test itself). **Cost:** Free (mocked external APIs). **Modularity:** Pass. **Performance:** Pass. |
| Improvement proposals / Alternatives | Ensure execution in CI with test matrix across both SQLite and PostgreSQL backends. |
