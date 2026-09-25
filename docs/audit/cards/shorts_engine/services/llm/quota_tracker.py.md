# Assessment Cards: shorts_engine/services/llm/quota_tracker.py

---

## Unit 1: Module Constants & Key Masking Helpers (mask_api_key, hash_api_key)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/quota_tracker.py:22-47` |
| What it does | Defines estimated daily quota limits per provider (`groq`, `gemini`, `openrouter`, `openai`, `youtube`) and provides non-reversible SHA256 key hashing and masking helpers (`sk-1234...5678`) to prevent credential leakage. |
| Industrial-grade? | **A** - Secure credential masking and deterministic truncation preventing secret exposure in dashboard tables and JSON files. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure functions). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (prevents API key leakage). **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: APIUsageTracker.__new__ & __init__
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/quota_tracker.py:49-76` |
| What it does | Thread-safe singleton constructor guarded by class mutex; creates fresh instances when explicit test paths are passed. |
| Industrial-grade? | **A** - Supports testing with isolated file paths without polluting global singleton state. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (`threading.Lock` + `threading.RLock`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass (`settings.STORAGE_DIR`). **Testability:** Pass (test injection path supported). **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Parameterized singleton with test path override (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: State Persistence (_load_state, _save_state, _ensure_today)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/quota_tracker.py:77-105, 358-368` |
| What it does | Manages JSON persistence and automatic calendar-day rollover, resetting daily counters (`requests_today`, `rate_limit_hits_today`, `cooldown_until`) while preserving cumulative `total_requests`. |
| Industrial-grade? | **B** - Excellent daily rollover logic, but `_save_state` uses direct `write_text` without an atomic temp-file replace. |
| Checklist | **Error handling:** Pass (catches JSON load errors, re-initializes fresh). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass (guarded by `_mutex`). **Atomicity:** Fail (non-atomic file overwrite). **External-dependency:** Pass. **Security:** Pass (only hashed/masked keys written). **Performance:** Pass. **Observability:** Pass (logs warning on read/write failure). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If the Python process is abruptly terminated during `write_text`, `api_key_usage.json` can be left truncated or empty. |
| Improvement | Use atomic write pattern: write to `api_key_usage.json.tmp` and `os.replace`. |
| Better approach? | Option 1: Atomic `os.replace` via temp file (Recommended). Option 2: Direct overwrite (Current - crash hazard). |
| Priority / Effort | P2 / S |

---

## Unit 4: Cooldown & Rate-Limit Tracking (is_cooling_down, set_cooldown, record_rate_limit)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/quota_tracker.py:110-127, 226-252` |
| What it does | Manages temporary cooldown states for API keys encountering HTTP 429 rate limits, preventing callers from sending requests to rate-limited keys. |
| Industrial-grade? | **A** - Thread-safe cooldown eviction based on wall-clock epoch timestamps. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (cooldown duration). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (`_mutex`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (logs cooldown onset). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: Live Telemetry & In-Flight Tracking (set_active_call, clear_active_call, record_failover_event, get_live_telemetry)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/quota_tracker.py:128-225` |
| What it does | Tracks in-flight LLM calls with live millisecond elapsed timers, records provider failover events in a 20-element ring buffer, and produces telemetry snapshots for dashboard UI. |
| Industrial-grade? | **A** - Low-latency thread-safe state container powering real-time dashboard banner updates. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (ring buffer caps memory at 20 entries). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (masks keys in live telemetry). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: Request Recording & Groq Header Telemetry (record_request, get_groq_remaining_tokens)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/quota_tracker.py:253-356` |
| What it does | Records latency, status, and model metadata per request, extracts Groq rate-limit headers (`x-ratelimit-remaining-tokens`, `x-ratelimit-reset-tokens`), and captures tokens/sec throughput and completion time. |
| Industrial-grade? | **A** - Rich diagnostic telemetry extraction directly from upstream API response headers. |
| Checklist | **Error handling:** Pass (defensive header parsing with type casting). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 7: Metrics Aggregation & Key Reconciliation (get_per_key_metrics, remove_key, get_summary)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/quota_tracker.py:369-575` |
| What it does | Reconciles active keys from settings with persistent tracker state, registers newly configured keys automatically, removes orphaned keys, and computes aggregate quota percentages. |
| Industrial-grade? | **B** - High-utility metric synthesis, but contains a duplicate `remove_key` method definition (lines 369-381 and lines 523-540). |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (prunes stale keys). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Warning (shadowed duplicate method). |
| Weakness or risk | `remove_key` is defined twice in the same class (line 369 and line 523), causing the first definition to be shadowed and creating dead code. |
| Improvement | Delete the earlier redundant definition of `remove_key`. |
| Better approach? | Option 1: Clean single method definition (Recommended). Option 2: Duplicate definition (Current). |
| Priority / Effort | P2 / S |
