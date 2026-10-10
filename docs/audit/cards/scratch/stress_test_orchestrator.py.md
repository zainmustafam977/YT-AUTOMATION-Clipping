# Assessment Cards: scratch/stress_test_orchestrator.py

---

## Unit 1: Hardcore Integration Stress Test & Telemetry Battery
| Field | Content |
|---|---|
| Location | `scratch/stress_test_orchestrator.py:1-516` |
| What it does | End-to-end integration and stress test harness exercising the entire pipeline across 8 test stages: 1) Hardware encoding & ONNX asset verification, 2) Long-form transcript selection via LLM router, 3) 5 caption presets generation and ASS transcoding, 4) Full 9:16 portrait rendering with YuNet CV face-tracking and EBU R128 audio mastering, 5) Multi-tier quality gate verification, 6) Universal recovery and auto-fix execution, 7) Bayesian multi-armed bandit optimization & circuit breaker tripping, 8) SubprocessWatchdog safety and hot SQLite backup creation. Exports comprehensive telemetry report to `storage/logs/hardcore_stress_test_report.json`. |
| Industrial-grade? | **A** - Comprehensive, realistic integration test battery with granular timing telemetry, clean exception handling, and full pipeline coverage. |
| Checklist | **Error handling:** Pass (wraps tests in try/finally, gracefully handles missing transcripts or models). **Timeouts:** Pass (enforces timeouts on subprocesses via `SubprocessWatchdog`). **Retries:** N/A. **Idempotency:** Pass (writes stress test outputs to isolated directories). **Input validation:** Pass. **Resource cleanup:** Pass (closes database sessions). **Concurrency safety:** Pass. **Atomicity:** Pass. **Secret hygiene:** Pass (masks API keys, queries settings). **Logging:** Pass. **Telemetry:** Pass (collects JSON metrics and duration timings for every subsystem). **Testability:** High (serves as top-level system verification harness). **Cost:** Reasonable (local processing + LLM selection). **Modularity:** Pass. **Performance:** Pass. |
| Improvement proposals / Alternatives | Consider moving from `scratch/` into `tests/integration/` as a permanent high-load regression and performance suite. |
