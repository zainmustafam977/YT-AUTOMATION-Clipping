# Assessment Cards: shorts_engine/services/execution/watchdog.py

---

## Unit 1: Exception Classes (SubprocessTimeoutError, SubprocessExecutionError)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/execution/watchdog.py:27-51` |
| What it does | Structured exception hierarchy providing detailed diagnostic messages including command array, timeout limit, exit code, and tail of stderr/stdout buffers. |
| Industrial-grade? | **A** - Truncates output to last 500 characters to prevent buffer bloat while preserving root cause errors. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: ExecutionResult Dataclass
| Field | Content |
|---|---|
| Location | `shorts_engine/services/execution/watchdog.py:53-67` |
| What it does | Data structure encapsulating execution exit code, stdout, stderr, execution wall-clock duration in milliseconds, timeout flag, and cleaned artifact paths. |
| Industrial-grade? | **A** - Rich, immutable return model with property `is_success`. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: SubprocessWatchdog.kill_process_tree
| Field | Content |
|---|---|
| Location | `shorts_engine/services/execution/watchdog.py:76-116` |
| What it does | Recursively discovers all child and descendant PIDs for a given parent PID via `psutil.Process.children(recursive=True)` and executes two-stage termination: graceful `SIGTERM`, waits up to 1.5s grace period, followed by hard `SIGKILL` for survivors. |
| Industrial-grade? | **A** - Guarantees zero zombie processes when child utilities (FFmpeg, yt-dlp) hang or spawn worker threads. |
| Checklist | **Error handling:** Pass (Catches `psutil.NoSuchProcess` and `AccessDenied`). **Timeouts:** Pass (1.5s grace timeout). **Retries:** Pass (Two-stage SIGTERM -> SIGKILL). **Idempotency:** Pass (Multiple calls safe). **Input validation:** Pass (Validates PID). **Resource cleanup:** Pass (Closes process tree completely). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`psutil`). **Security:** Pass. **Performance:** Pass (Low-overhead process discovery). **Observability:** Pass (Logs warning on stubborn PIDs requiring SIGKILL). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | On Windows, SIGTERM and SIGKILL are both mapped to `TerminateProcess` by psutil, which is immediate; this is standard Windows semantics. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep psutil recursive tree kill (Recommended - industry standard). Option 2: Windows Job Objects (Windows-only, non-portable to Linux containers). |
| Priority / Effort | P3 / S |

---

## Unit 4: SubprocessWatchdog.run
| Field | Content |
|---|---|
| Location | `shorts_engine/services/execution/watchdog.py:118-274` |
| What it does | Supervised execution harness. Spawns subprocess with UTF-8 pipes, monitors wall-clock time against `timeout_sec`, kills process tree on timeout or exception, purges partial artifacts, and transparently passes mocked calls during pytest runs. |
| Industrial-grade? | **A** - Complete process lifecycle supervision with automated failure cleanup and mock detection for testability. |
| Checklist | **Error handling:** Pass (Structured exceptions with output tail). **Timeouts:** Pass (Guaranteed hard timeout enforcement). **Retries:** N/A (Delegated to caller). **Idempotency:** Pass. **Input validation:** Pass (Converts string/Path objects). **Resource cleanup:** Pass (Kills orphan tree and purges incomplete files). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (Uses list arguments, avoids `shell=True` to prevent command injection). **Performance:** Pass. **Observability:** Pass (Debug execution logs and stdout capture). **Config:** Pass (`timeout_sec` parameter). **Testability:** Pass (Explicitly detects `mock_calls` or monkeypatched `subprocess.run`). **Readability:** Pass. |
| Weakness or risk | Very high output volume on long-running commands could theoretically exceed memory if buffered in memory indefinitely; however, yt-dlp and ffmpeg are invoked with quiet or progress flags. |
| Improvement | Add an optional buffer size cap for stdout/stderr in future high-throughput streaming jobs. |
| Better approach? | Option 1: Keep in-memory buffer with pipe communication (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: SubprocessWatchdog._cleanup_artifacts
| Field | Content |
|---|---|
| Location | `shorts_engine/services/execution/watchdog.py:275-298` |
| What it does | Unlinks files or deletes directory trees registered in `artifacts_to_cleanup` if execution fails or times out. |
| Industrial-grade? | **A** - Prevents corrupted partial outputs (e.g. half-rendered MP4s) from lingering on disk after crashes. |
| Checklist | **Error handling:** Pass (Catches `Exception` and logs warning). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (Logs purged artifacts). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Safely checks `.exists()` before deleting. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |
