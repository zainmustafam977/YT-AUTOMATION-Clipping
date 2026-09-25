# Assessment Cards: shorts_engine/services/llm/router.py

---

## Unit 1: LLMRouter.__init__ & Multi-Key Pool Hydration
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:47-115` |
| What it does | Loads multi-key pools for Groq, Gemini, OpenRouter, and OpenAI from environment or runtime settings, initializes round-robin indices, and configures model identifiers. |
| Industrial-grade? | **A** - Supports multi-key arrays per provider with seamless single-key backward compatibility and dynamic runtime config reloads. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (strips whitespace, filters empty keys). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (keys masked in all downstream calls). **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Multi-key pool with fallback to settings (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Round-Robin Key Ordering & Gemini Client-Side Pacing (_get_ordered_keys)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:116-130, 604-613` |
| What it does | Implements thread-safe round-robin key rotation that partitions keys into active vs cooling-down subsets, and enforces client-side pacing (4.0s minimum interval per key) to prevent exceeding Gemini's 15 RPM free-tier rate limit. |
| Industrial-grade? | **A** - Proactive client-side pacing eliminates rate-limit spikes before requests leave the host. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (pacing interval delay). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (`_rr_lock` and `_gemini_pacing_lock`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass (`settings.GEMINI_PACING_SEC`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Pacing timestamps are in-process memory. If multiple worker processes run concurrently without shared IPC, pacing could allow concurrent bursts across processes. |
| Improvement | Support Redis-backed distributed token bucket for multi-worker container deployments. |
| Better approach? | Option 1: In-process thread-safe pacing lock (Current - optimal for single-process / threaded runner). Option 2: Redis distributed rate limiter (Recommended for horizontal multi-container scale). |
| Priority / Effort | P2 / M |

---

## Unit 3: SDK Client Construction & Availability Properties
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:132-189` |
| What it does | Lazily instantiates `google.genai.Client` with configurable HTTP timeout options and exposes boolean availability properties for each provider tier. |
| Industrial-grade? | **A** - Clean lazy instantiation with property mock support for unit tests. |
| Checklist | **Error handling:** Pass (logs warning on SDK init failure). **Timeouts:** Pass (configures `GEMINI_TIMEOUT_SEC`). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`google-genai`). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass (checks class property descriptors for test mocking). **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: Rate Limit Detection & Delay Extraction (_is_rate_limit_error, _extract_retry_delay)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:191-238` |
| What it does | Detects HTTP 429 and RESOURCE_EXHAUSTED exceptions, parsing exact delay recommendations from `Retry-After` headers and regex matching in JSON error text (`retry in 7.5s`). |
| Industrial-grade? | **A** - Dynamic parsing of upstream provider backoff hints prevents guessing backoff durations. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure regex parsing). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Upstream retry delay extraction (Recommended - avoids wasteful static sleeps). |
| Priority / Effort | P3 / S |

---

## Unit 5: LLMRouter.generate_structured & Failover Orchestration
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:239-360` |
| What it does | Central LLM entrypoint: executes token estimation pre-flight, dynamically re-ranks tiers (routes large >5,500 token prompts directly to Gemini's 1M context window to prevent Groq 8,000 TPM limit overruns), iterates across providers with real-time failover telemetry, and trips the Tier 5 Heuristic Circuit Breaker if all cloud providers fail. |
| Industrial-grade? | **A** - Industrial-grade fault tolerance: zero single point of failure, payload-aware routing, live failover audit trail, and guaranteed deterministic fallback. |
| Checklist | **Error handling:** Pass (catches all provider exceptions, records failover trail). **Timeouts:** Pass (per-provider timeouts). **Retries:** Pass (multi-tier failover). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (token pre-flight prevents HTTP 413). **Observability:** Pass (records failover events and telemetry). **Config:** Pass (`GROQ_TOKEN_THRESHOLD`, `LLM_FALLBACK_ON_RATE_LIMIT`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Algorithmic heuristic fallback guarantees pipeline never hangs or halts when all cloud APIs are exhausted. |
| Improvement | Keep as is. |
| Better approach? | Option 1: 5-Tier dynamic quality-ranked failover with token pre-flight and heuristic circuit breaker (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: _call_groq
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:362-550` |
| What it does | Calls Groq Cloud LPU completions via `httpx` with multi-key round-robin, pre-flight remaining token check, automatic 404 deprecated model blacklisting, short-duration (<=15s) 429 inline retry, and inference metric parsing. |
| Industrial-grade? | **A** - High-speed LPU integration (~800 tokens/sec) with resilient model fallback (`llama-3.3-70b-versatile` -> `llama-3.1-8b-instant`). |
| Checklist | **Error handling:** Pass (handles 404 deprecations, 413 oversized payloads, 429 rate limits). **Timeouts:** Pass (45.0s httpx timeout). **Retries:** Pass (inline retry for brief 429s, key failover for longer cooldowns). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (`with httpx.Client(...)`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Groq API). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (captures tokens/sec and latency). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Multi-model LPU pipeline with dynamic model deprecation blacklist (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 7: _call_gemini
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:552-829` |
| What it does | Executes Gemini structured generation using official `google-genai` SDK: attempts modern `client.interactions.create` (Interactions API) first, falls back to `client.models.generate_content`, enforces client-side pacing lock, and cycles backup models (`gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-1.5-flash`). |
| Industrial-grade? | **A** - Dual API support (Interactions + generateContent) adhering to Google DeepMind recommended practices, combined with hardware/quota pacing. |
| Checklist | **Error handling:** Pass (handles 429, 503 transient service overloads). **Timeouts:** Pass (`GEMINI_TIMEOUT_SEC`). **Retries:** Pass (auto-retries 503 and brief 429s). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (`finally: clear_active_call`). **Concurrency safety:** Pass (`_gemini_pacing_lock`). **Atomicity:** Pass. **External-dependency:** Pass (`google-genai`). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Modern google-genai Interactions API with generate_content fallback (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 8: _call_openrouter & _call_openai
| Field | Content |
|---|---|
| Location | `shorts_engine/services/llm/router.py:831-1077` |
| What it does | Backup Tier 3 and Tier 4 integrations: executes HTTP Chat Completions with round-robin key rotation, free-model slug fallbacks on OpenRouter, and strict JSON schema system prompt enforcement. |
| Industrial-grade? | **A** - Complete tertiary and quaternary backup channels with rate-limit tracking and clean response parsing. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (45s timeout). **Retries:** Pass (key rotation). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (OpenRouter / OpenAI APIs). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |
