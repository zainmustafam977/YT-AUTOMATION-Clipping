# Assessment Cards: shorts_engine/orchestration/lifecycle.py

---

## Unit 1: LifecycleManager
| Field | Content |
|---|---|
| Location | `shorts_engine/orchestration/lifecycle.py:13-57` |
| What it does | Manages state machine transitions for `SourceVideo` and `Clip` models with `expected_status` pre-condition assertions to protect against race conditions and out-of-order transitions. |
| Industrial-grade? | **B+** - Explicit state transition guardrails, but verifies expected state in-memory rather than via atomic SQL predicate. |
| Checklist | **Error handling:** Pass (logs warnings and returns False on mismatch). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Needs improvement (in-memory check before commit does not prevent concurrent session race). **Atomicity:** Pass (commits on state change). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (structured state transition logs). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | The state comparison (`video.status != expected_status`) evaluates the Python object in memory before calling `db.commit()`. If another worker updated the row in the DB concurrently, this Python check passes against a stale object snapshot. |
| Improvement | Use an atomic database UPDATE query with WHERE clause: `db.query(SourceVideo).filter(id == video.id, status == expected_status).update({"status": target_status})` and verify row count > 0. |
| Better approach? | Option 1: Atomic SQL CAS update (Recommended). Option 2: In-memory attribute check (Current). |
| Priority / Effort | P2 / S |
