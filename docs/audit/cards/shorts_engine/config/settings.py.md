# Assessment Cards: shorts_engine/config/settings.py

---

## Unit 1: PipelineSettings Configuration Base & Path Layout
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:1-40` |
| What it does | Defines `PipelineSettings(BaseSettings)` with `.env` file loading, case-insensitivity, extra attribute tolerance, and root filesystem directory anchoring. |
| Industrial-grade? | **A** - Leverages Pydantic Settings v2 with strict Path resolution derived from repo root. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (Pydantic type coercion). **Resource cleanup:** N/A. **Concurrency safety:** Pass (Immutable path anchors). **Atomicity:** Pass. **External-dependency:** Pass (`pydantic_settings`). **Security:** Pass (Reads from local `.env` with fallback). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If `extra="allow"` is set on model_config, typos in `.env` names could be silently ignored instead of raising validation errors. |
| Improvement | Add a warning logger for unrecognized environment variables while keeping `extra="allow"` for dynamic extension. |
| Better approach? | Option 1: Keep `extra="allow"` with startup diff logging (Recommended). Option 2: `extra="forbid"` (Strict, but breaks backwards compatibility with custom extensions). |
| Priority / Effort | P2 / S |

---

## Unit 2: Master Secret & Key Derivation Parameters
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:41-48` |
| What it does | Defines `MASTER_ENCRYPTION_KEY` and `ENCRYPTION_SALT` for PBKDF2/AES-256-GCM token encryption. |
| Industrial-grade? | **B** - Development default is present, but validated against staging/production abuse in `validate_bounds`. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (Guarded in `validate_bounds` for staging/prod). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (Enforces 32-byte entropy in non-dev envs). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | In local development, the default key is used; if an operator deploys with `ENV=development` to the cloud, default secrets would be exposed. |
| Improvement | Enforce that `MASTER_ENCRYPTION_KEY` must never be default if `DASHBOARD_HOST` is bound to `0.0.0.0` or non-loopback interface. |
| Better approach? | Option 1: Add bind address validation check in `validate_bounds` (Recommended). Option 2: Force generating a random secret in `.env` during `setup_environment.py`. |
| Priority / Effort | P1 / S |

---

## Unit 3: Multi-Provider LLM Configuration
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:49-110` |
| What it does | Configures 4 LLM providers (Groq, Gemini, OpenRouter, OpenAI), pacing intervals, token thresholds (5,500 tokens), and failover toggles. |
| Industrial-grade? | **A** - Complete multi-tier routing configuration with token ceiling thresholds and quota protection. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (`GEMINI_TIMEOUT_SEC` constrained between 5s and 120s). **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass (Validated bounds on pacing, timeouts, and temperature). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (API keys masked in logging/dashboard). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Pydantic constraints ensure numerical fields cannot be assigned negative or out-of-range values. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep current Pydantic schema (Recommended). Option 2: Externalize provider schemas to plugins. |
| Priority / Effort | P3 / S |

---

## Unit 4: Video Compositing, CRF, & Dead-Zone Ratios
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:153-194` |
| What it does | Configures 1080x1920 vertical canvas, Lanczos scaling, CRF 18 visually lossless encoding, and safe-zone margins (bottom 20%, right 15%). |
| Industrial-grade? | **A** - Follows YouTube Shorts and broadcast video encoding standards (CRF 18, high profile, level 4.2). |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (`CRF` constrained 15..28, `FPS` constrained 24..60). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Safe-zone dead-zone ratios match YouTube mobile UI geometry. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: Audio Mastering & EBU R128 Limits
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:237-253` |
| What it does | Sets target integrated loudness (-14 LUFS), true peak (-1.5 dBFS), loudness range (7.0 LU), ducking attenuation (-12 dB), and 80Hz rumble filter. |
| Industrial-grade? | **A** - Exact conformance with AES/EBU streaming loudness standards. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Matches YouTube Shorts broadcast profile. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: Bayesian Multi-Armed Bandit Tuning Parameters
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:290-315` |
| What it does | Defines 120s evaluation intervals, 100 impression minimums, 95% posterior probability threshold, and 50% velocity drop emergency circuit breaker. |
| Industrial-grade? | **A** - Robust statistical bounds preventing premature convergence or traffic destruction. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (`AB_CONFIDENCE_THRESHOLD` bounded 0.80..0.999). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 7: PipelineSettings.validate_bounds Validator
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:342-395` |
| What it does | Enforces cross-field invariant logic (min duration $\le$ max duration, min segments $\le$ max segments), blocks default credentials in staging/prod, and creates required filesystem directories. |
| Industrial-grade? | **A** - Model-level post-validation prevents invalid runtime states from ever initializing. |
| Checklist | **Error handling:** Pass (Raises informative `ValueError`). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (`mkdir(parents=True, exist_ok=True)`). **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (Blocks weak passwords and default secret keys in production). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 8: Key Pool Accessors & Runtime Mutation Methods
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:396-525` |
| What it does | Provides `get_gemini_keys`, `get_groq_keys`, `get_openrouter_keys`, `get_openai_keys`, `add_api_key`, and `remove_api_key` with SHA256 masking. |
| Industrial-grade? | **A** - Handles multi-line string input, comma-separated lists, duplicate deduplication, and safe key deletion by hash. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (Rejects empty keys or masked bullet strings `••••`). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (Prevents storing masked placeholder strings). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Key pool mutation in memory (`add_api_key`) does not persist back to `.env` automatically; relies on `save_runtime_settings()` to persist to `runtime_settings.json`. |
| Improvement | Ensure `save_runtime_settings()` is explicitly invoked whenever keys are added or removed. |
| Better approach? | Option 1: Keep memory mutation decoupled with atomic JSON persistence (Recommended). |
| Priority / Effort | P2 / S |

---

## Unit 9: Global Singleton Instantiation
| Field | Content |
|---|---|
| Location | `shorts_engine/config/settings.py:527-529` |
| What it does | Instantiates `settings = PipelineSettings()` as the global configuration singleton. |
| Industrial-grade? | **A** - Standard Python singleton pattern for configuration. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |
