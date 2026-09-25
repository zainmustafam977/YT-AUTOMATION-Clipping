# Assessment Cards: shorts_engine/config/logging_config.py

---

## Unit 1: Test Environment Detection & Dynamic Log Path Resolution
| Field | Content |
|---|---|
| Location | `shorts_engine/config/logging_config.py:25-39` |
| What it does | Detects test environments (`pytest` in `sys.modules`, `PYTEST_CURRENT_TEST` env var, or `ENV=test`) and routes log writes to `shorts_engine_test.log` instead of polluting production `shorts_engine.log`. |
| Industrial-grade? | **A** - Clean separation of production telemetry from test fixture noise. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (Fast dictionary lookup). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: setup_logging Initializer
| Field | Content |
|---|---|
| Location | `shorts_engine/config/logging_config.py:41-86` |
| What it does | Configures console output (`sys.stdout`) and a 10MB rotating file handler (`RotatingFileHandler(backupCount=5)`) writing to `storage/logs/shorts_engine.log`. Deduplicates handlers if called repeatedly. |
| Industrial-grade? | **A** - Prevents unbounded disk log growth (caps total log volume at 50 MB) and prevents duplicate logging handler proliferation. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (Checks for existing handler before adding). **Input validation:** Pass (`level` parameter). **Resource cleanup:** Pass (Handler file descriptor managed cleanly). **Concurrency safety:** Pass (Thread-safe logging lock). **Atomicity:** Pass. **External-dependency:** Pass (Standard library `logging`). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (Propagates to `uvicorn`, `uvicorn.access`, and `shorts_engine`). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Log output is currently text formatted (`%(asctime)s [%(levelname)s] ...`). In multi-container microservice deployments (e.g. Datadog, ELK), JSON-structured logging is preferred. |
| Improvement | Add an optional `JSONFormatter` toggle controlled by `settings.JSON_LOGS_ENABLED`. |
| Better approach? | Option 1: Keep standard text formatting with optional JSON toggle (Recommended). Option 2: Full structlog rewrite (Heavier dependency footprint). |
| Priority / Effort | P2 / S |

---

## Unit 3: reload_runtime_settings
| Field | Content |
|---|---|
| Location | `shorts_engine/config/logging_config.py:88-129` |
| What it does | Reads `storage/config/runtime_settings.json` and updates attributes on the in-memory `settings` singleton with automated type casting (bool, list, int, float, str). Disables in test mode unless overridden. |
| Industrial-grade? | **B** - Highly effective for hot-reloading without process restarts, but lacks atomic lock protection if a concurrent worker process writes to the JSON file while another reads it. |
| Checklist | **Error handling:** Pass (Catches JSON parse and file errors gracefully). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (Type coercion with error logging). **Resource cleanup:** Pass (Uses context manager `with open`). **Concurrency safety:** Fail (File read is not protected by an inter-process file lock like `fasteners` or `msvcrt/fcntl`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (Only reads when requested). **Observability:** Pass. **Config:** Pass. **Testability:** Pass (Isolated from test suite). **Readability:** Pass. |
| Weakness or risk | On Windows or multi-worker Linux, if `save_runtime_settings` writes at the exact moment a worker calls `reload_runtime_settings`, a partial JSON read or file-locking error could occur. |
| Improvement | Use atomic replace (`Path.replace`) when writing, and wrap file reads in a retry loop if JSON decoding fails due to in-flight writes. |
| Better approach? | Option 1: Atomic file write via temporary file + replace (Recommended). Option 2: Redis pub/sub config bus (Requires external server, unsuitable for zero-dependency standalone mode). |
| Priority / Effort | P1 / S |

---

## Unit 4: save_runtime_settings
| Field | Content |
|---|---|
| Location | `shorts_engine/config/logging_config.py:131-153` |
| What it does | Reads existing runtime config, merges dictionary updates, saves to `runtime_settings.json`, and invokes `reload_runtime_settings()` to apply changes immediately. |
| Industrial-grade? | **B** - Writes directly to destination file without atomic temporary file swap. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Fail (Non-atomic file write). **Atomicity:** Fail (Writing directly to `runtime_settings.json` can expose a truncated file if the process crashes mid-write). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (Logs count of applied overrides). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Power failure or unexpected process termination during `json.dump` leaves a corrupted or 0-byte `runtime_settings.json`. |
| Improvement | Write to `runtime_settings.json.tmp` first, flush, fsync, and call `os.replace` to guarantee atomicity. |
| Better approach? | Option 1: Write-to-temp and atomic rename (Recommended - standard POSIX/NT atomic pattern). Option 2: Keep direct write (Fragile under crash conditions). |
| Priority / Effort | P1 / S |
