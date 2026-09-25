# Module Reference: 03 Multi-Provider LLM Gateway & Quota Router

> **Path:** `shorts_engine/services/llm/`  
> **Source Files:**
> - [`shorts_engine/services/llm/router.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/llm/router.py)
> - [`shorts_engine/services/llm/quota_tracker.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/llm/quota_tracker.py)

---

## 1. Module Purpose & Responsibilities

This subsystem provides:
1. **Tiered Zero-Cost LLM Routing:** Multi-provider inference gateway with intelligent priority sorting:
   - **Tier 1 (Speed):** Groq LPU (`llama-3.3-70b-versatile`, `mixtral-8x7b-32768`, `openai/gpt-oss-120b`).
   - **Tier 2 (Capacity):** Google Gemini via `google-genai` (`gemini-2.0-flash`, `gemini-1.5-pro`).
   - **Tier 3 (Aggregated Open Weights):** OpenRouter free models (`meta-llama/llama-3.3-70b-instruct:free`).
   - **Tier 4 (Paid Fallback):** OpenAI (`gpt-4o-mini`).
   - **Tier 5 (Continuity Circuit Breaker):** Deterministic algorithmic heuristic fallback ensuring pipeline continuity even under total network/cloud outage.
2. **Dynamic Token & Context Pre-flight:** Payloads exceeding `GROQ_TOKEN_THRESHOLD` (5,500 tokens) automatically bypass Groq (preventing HTTP 413 / TPM exhaustion) and route directly to Gemini (1M token context window).
3. **Multi-Key Pool & Round-Robin Load Balancing:** Rotates multiple API keys per provider, tracking per-key requests, latencies, and 60-second HTTP 429 cooldowns in `storage/api_key_usage.json`.
4. **Client-Side Request Pacing:** Enforces minimum 4.0-second delay (`GEMINI_PACING_SEC`) per Gemini key to strictly guard the 15 RPM Free Tier ceiling.

---

## 2. Public Interfaces & Signatures

### 2.1 `shorts_engine.services.llm.router.LLMRouter`
- `__init__(gemini_api_key=None, groq_api_key=None, openrouter_api_key=None, openai_api_key=None, gemini_api_keys=None, groq_api_keys=None, openrouter_api_keys=None, openai_api_keys=None, gemini_model=None, groq_model=None, openrouter_model=None, openai_model=None, provider=None, tracker=None)`: Configures provider key pools and models.
- `generate_structured(prompt: str, schema: Type[T], temperature: float = 0.2, video_id: Optional[str] = None) -> Tuple[Optional[T], Dict[str, Any]]`:
  - Enforces JSON output parsing directly into Pydantic schema `T`.
  - Returns parsed model instance and execution metadata (`provider`, `model`, `latency_ms`, `failover_trail`, `is_failover`).
- `is_groq_available -> bool`, `is_gemini_available -> bool`, `is_openrouter_available -> bool`, `is_openai_available -> bool`: Availability check properties.

### 2.2 `shorts_engine.services.llm.quota_tracker.APIUsageTracker`
- `record_request(provider: str, key: str, latency_ms: float, tokens_estimated: int = 0) -> None`: Logs successful API call.
- `record_rate_limit(provider: str, key: str, cooldown_seconds: float = 60.0, status_code: int = 429) -> None`: Places key into temporary cooldown.
- `is_cooling_down(provider: str, key: str) -> bool`: Checks if key is in active backoff.
- `get_key_stats(provider: str, key: str) -> Dict[str, Any]`: Returns metrics (`requests_today`, `rate_limit_hits_today`, `avg_latency_ms`).
- `record_failover_event(video_id: str, from_provider: str, to_provider: str, reason: str) -> None`: Broadcasts live failover event.
- `usage_tracker`: Global singleton instance.

---

## 3. Intelligent Tiered Failover Flow

```mermaid
flowchart TD
    A[generate_structured: prompt, schema] --> B{Explicit Provider Selected?}
    B -->|Yes| C[Call Selected Provider]
    C -->|Success| D[Return Structured Pydantic Result]
    C -->|Fail & Fallback Disabled| E[Return None]
    C -->|Fail & Fallback Enabled| F[Enter Dynamic Failover Chain]

    B -->|No 'auto'| G{Prompt Tokens >= 5,500?}
    G -->|Yes: Large Payload| H[Rank Gemini First (1M Context)]
    G -->|No: Standard Payload| I[Rank Groq First (Speed LPU)]

    H --> F
    I --> F

    subgraph Dynamic_Failover_Chain [Tiered Quality-Ranked Routing]
        F --> T1{Tier 1: Groq LPU}
        T1 -->|Ready & Keys Available| T1_CALL[Call Groq API]
        T1_CALL -->|200 OK & Schema Valid| D
        T1_CALL -->|429 Rate Limit / Error| T1_COOL[Apply 60s Cooldown to Key]
        T1_COOL --> T2

        T1 -->|Cooling Down / Failed| T2{Tier 2: Google Gemini}
        T2 -->|Pacing Guard: 4.0s| T2_CALL[Call Gemini API via google-genai]
        T2_CALL -->|200 OK & Schema Valid| D
        T2_CALL -->|429 / 503 Error| T2_COOL[Apply 60s Cooldown to Key]
        T2_COOL --> T3

        T2 -->|Cooling Down / Failed| T3{Tier 3: OpenRouter}
        T3 -->|Ready| T3_CALL[Call OpenRouter Free Models]
        T3_CALL -->|200 OK & Schema Valid| D
        T3_CALL -->|Failed| T4

        T3 -->|Failed| T4{Tier 4: OpenAI}
        T4 -->|Configured| T4_CALL[Call gpt-4o-mini]
        T4_CALL -->|200 OK & Schema Valid| D
        T4_CALL -->|Failed| T5

        T4 -->|Failed| T5[Tier 5: Heuristic Circuit Breaker]
        T5 -->|Deterministic Word Scoring| J[Algorithmic Fallback Candidate]
        J --> D
    end
```

---

## 4. State & Lifecycle

| Component | State Stored | Storage Location | Concurrency / Lifecycle |
| :--- | :--- | :--- | :--- |
| `APIUsageTracker` | Key daily requests, 429 hit counts, cooldown timestamps | `storage/api_key_usage.json` | Thread-safe `threading.RLock`, auto-resets counters on new UTC day. |
| `LLMRouter` | Round-robin counters (`_rr_counters`), Gemini pacing timestamps (`_gemini_last_call_time`) | In-memory class attributes with `threading.Lock` | Ephemeral; shared across all router instances. |
| Active Call Telemetry | Current active API call details (provider, key mask, timestamp) | In-memory `_active_call` | Read by dashboard SSE route for real-time live banner. |

---

## 5. Security & Invariants

1. **Intra-Provider Round-Robin:** `_get_ordered_keys()` cycles through key pools to distribute load across multiple developer accounts.
2. **Prompt Injection & Formatting Defense:** Structured generation requires all providers to return strict JSON matching Pydantic schemas. Output is validated with `schema.model_validate_json()`; invalid JSON triggers provider retry or failover.
3. **Key Masking:** `mask_api_key()` displays only 8-character prefix and 4-character suffix (`••••••••...xxxx`), ensuring raw secrets never appear in logs or the web UI.
