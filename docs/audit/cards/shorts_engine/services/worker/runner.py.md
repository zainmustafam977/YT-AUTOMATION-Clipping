# Assessment Cards: shorts_engine/services/worker/runner.py

---

## Unit 1: clear_cuda_cache & PID Liveness Utilities
| Field | Content |
|---|---|
| Location | `shorts_engine/services/worker/runner.py:34-46, 104-144` |
| What it does | `clear_cuda_cache` purges PyTorch CUDA VRAM allocations and IPC cache between jobs. `is_pid_alive` and `extract_pid_from_claim` perform cross-platform process liveness checking (`psutil`, Win32 `OpenProcess`, POSIX `kill(0)`) to detect dead worker containers holding claims. |
| Industrial-grade? | **A** - Robust cross-platform memory sanitation and PID inspection preventing VRAM fragmentation and zombie claims. |
| Checklist | **Error handling:** Pass (gracefully catches missing PyTorch or OS permissions). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (optional PyTorch, psutil, ctypes). **Security:** Pass. **Performance:** Pass (fast kernel handle query). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Native OS kernel PID inspection (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Queue Claiming & Stale Claim Reconciliation
| Field | Content |
|---|---|
| Location | `shorts_engine/services/worker/runner.py:48-102, 146-240` |
| What it does | Implements dialect-aware database queue locking: PostgreSQL engine uses `FOR UPDATE SKIP LOCKED` for non-blocking multi-worker concurrency; SQLite engine uses atomic Compare-And-Swap (CAS) updates. `reconcile_stale_claims` detects crashed worker PIDs or expired leases (timeout: 600s) and resets claims safely. |
| Industrial-grade? | **A** - High-concurrency queue engine preventing double-renders and deadlocks across both distributed PostgreSQL and embedded SQLite environments. |
| Checklist | **Error handling:** Pass (rolls back on reconciliation error). **Timeouts:** Pass (600s default lease timeout). **Retries:** Pass. **Idempotency:** Pass (atomic claim markers). **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass (`FOR UPDATE SKIP LOCKED` on Postgres, CAS on SQLite). **Atomicity:** Pass (atomic database transaction). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (`SKIP LOCKED` eliminates contention). **Observability:** Pass (logs claim stealing and stale reclamation). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dialect-aware locking (`SKIP LOCKED` / CAS) (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: WorkerRunner.process_clip, run_once, run_loop
| Field | Content |
|---|---|
| Location | `shorts_engine/services/worker/runner.py:242-500` |
| What it does | Central worker loop: reloads runtime settings, cleans GPU memory, computes active-speaker crop plan, burns ASS subtitles, synchronizes SFX, renders 9:16 Short via FFmpeg compositor, creates cover thumbnail, runs Media QA and Multimodal Quality Gate checks, and manages retry backoff up to `MAX_RETRY_COUNT`. |
| Industrial-grade? | **A** - Industrial-grade job processor with end-to-end multi-stage QA inspection, crash safety, and automatic retry management. |
| Checklist | **Error handling:** Pass (comprehensive exception handling with retry counter increments). **Timeouts:** Pass (compositor watchdog timeouts). **Retries:** Pass (increments `retry_count` up to `settings.MAX_RETRY_COUNT`, resets to RENDERING on transient failures). **Idempotency:** Pass. **Input validation:** Pass (checks source media and candidate existence). **Resource cleanup:** Pass (`finally` block flushes CUDA cache and closes DB session). **Concurrency safety:** Pass (scoped DB sessions per job). **Atomicity:** Pass (state updates committed in discrete phases). **External-dependency:** Pass (FFmpeg, OpenCV, Torch). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (detailed log of render duration and QA results). **Config:** Pass (`reload_runtime_settings()` per poll). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None of significance. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current multi-stage worker processor (Recommended). |
| Priority / Effort | P3 / S |
