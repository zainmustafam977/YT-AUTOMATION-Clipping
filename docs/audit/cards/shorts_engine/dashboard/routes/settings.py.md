# Assessment Cards: shorts_engine/dashboard/routes/settings.py

---

## Unit 1: GET /settings & GET /api/settings
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/settings.py:1-130` |
| What it does | Serves the interactive settings configuration UI (`/settings`) and programmatic JSON endpoint (`/api/settings`). Prepares masked representations of API key pools across providers (Groq, Gemini, OpenRouter, OpenAI), constructs quota and model telemetry states, injects runtime parameter bounds dictionaries (`SETTINGS_BOUNDS`), and passes CSRF tokens. |
| Industrial-grade? | **A** - Secure credential masking, clear bounds specification, and robust permission gating. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_viewer`, credentials masked showing only first 4 and last 4 characters with SHA-256 fingerprints). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Masked key presentation with SHA-256 fingerprinting (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: POST /settings/update (Runtime Settings Mutation & Bounds Enforcement)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/settings.py:131-370` |
| What it does | Processes bulk configuration updates from the dashboard or REST clients. Strictly parses numeric and boolean fields, validates values against clamped constraints in `SETTINGS_BOUNDS` (min/max duration, aspect ratios, caption margins, CRF, Whisper parameters), commits changes to `data/runtime_settings.json`, updates in-memory `settings` singleton via `apply_runtime_settings()`, and supports HTMX, form redirects, and JSON responses. |
| Industrial-grade? | **A** - Resilient parameter validation with multi-format responses and audit logging. |
| Checklist | **Error handling:** Pass (logs errors, returns descriptive feedback). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (strict bound clamping across numeric, enum, and boolean settings). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass (atomic JSON write). **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (detailed audit log records modified keys and operator identity). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Multi-format response handler with bound-enforced persistence (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: POST /settings/keys/add & POST /settings/keys/delete (API Key Pool Management)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/settings.py:371-453` |
| What it does | Adds and removes individual API keys from runtime rotation pools for Groq, Gemini, OpenRouter, and OpenAI. Validates key strings (rejecting masked or empty values), adds them to in-memory provider lists, syncs them to `data/runtime_settings.json`, updates the usage tracker, and writes operator audit trails. |
| Industrial-grade? | **A** - Secure credential lifecycle management preventing masked overwrite regressions. |
| Checklist | **Error handling:** Pass (rejects invalid providers with HTTP 400). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (guards against masked dots `••••` and whitespace). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (operator identity logged). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: In-memory pool modification + persistent runtime JSON synchronization (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: POST /settings/keys/test (Live Provider Health Ping)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/settings.py:454-523` |
| What it does | Live testing of candidate API keys against upstream provider APIs (Groq `/openai/v1/models`, Gemini `/v1beta/models`, OpenRouter `/api/v1/auth/key`, OpenAI `/v1/models`). Measures round-trip latency in milliseconds and returns diagnostic status messages. |
| Industrial-grade? | **A-** - Practical live verification with explicit timeout boundaries. |
| Checklist | **Error handling:** Pass (catches network exceptions and formats HTTP error responses). **Timeouts:** Pass (explicit 6.0s timeout on httpx requests). **Retries:** N/A (single probe). **Idempotency:** Pass (read-only probe). **Input validation:** Pass (validates provider and key hash). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Groq, Google, OpenRouter, OpenAI). **Security:** Pass (`require_operator`, `verify_csrf`, sensitive keys never leaked in responses). **Performance:** Pass. **Observability:** Pass (latency reported). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Lightweight GET ping to provider models endpoint with bounded timeout (Recommended). |
| Priority / Effort | P3 / S |
