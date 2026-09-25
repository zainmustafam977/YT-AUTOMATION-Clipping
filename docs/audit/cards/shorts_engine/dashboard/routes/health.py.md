# Assessment Cards: shorts_engine/dashboard/routes/health.py

---

## Unit 1: liveness_probe (/livez)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/health.py:32-43` |
| What it does | Lightweight Kubernetes/Docker liveness probe returning HTTP 200, system uptime, and current Unix timestamp to verify the application event loop is answering requests. |
| Industrial-grade? | **A** - Non-blocking liveness probe. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** N/A. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** N/A. **Security:** Pass (unauthenticated public probe). **Performance:** Pass (sub-millisecond execution). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard Kubernetes liveness probe (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: readiness_probe (/readyz) & healthz_summary (/healthz)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/health.py:45-168` |
| What it does | Cloud-native readiness verification performing deep health checks across critical dependencies: measures database query latency (`SELECT 1`), verifies disk storage headroom (`free_gb > 1.0` required), checks presence of FFmpeg and FFprobe binaries via `shutil.which`, and checks host RAM/CPU headroom. Sets HTTP 503 Service Unavailable if critical checks fail. |
| Industrial-grade? | **A** - Comprehensive cloud-native readiness inspection with accurate dependency status codes. |
| Checklist | **Error handling:** Pass (gracefully catches database, disk, and tool exceptions without crashing). **Timeouts:** Pass (uses monotonic clock for DB latency). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** N/A. **Resource cleanup:** Pass (database session closed with context manager). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (SQLite/PostgreSQL, FFmpeg, psutil). **Security:** Pass (no sensitive credentials exposed in JSON response). **Performance:** Pass (lightweight non-blocking queries). **Observability:** Pass (detailed status dictionary and latency metrics). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Multi-dependency readiness probe (Recommended). |
| Priority / Effort | P3 / S |
