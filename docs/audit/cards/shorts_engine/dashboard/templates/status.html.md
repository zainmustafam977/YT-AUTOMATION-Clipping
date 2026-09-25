# Assessment Cards: shorts_engine/dashboard/templates/status.html

---

## Unit 1: Real-Time AI Telemetry Banner & Quota Monitor
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/status.html:1-172` |
| What it does | Displays the top live telemetry banner indicating LLM health, active invocation status, failover triggers, and cooling keys. Renders the detailed AI & API Key Quota Monitor table tracking daily request quotas, progress bars, remaining tokens per minute, reset countdowns, latency in milliseconds, and tokens per second throughput across Groq, Gemini, OpenRouter, and OpenAI pools. |
| Industrial-grade? | **A** - High observability dashboard displaying multi-tier provider health and token quotas. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (API keys masked). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Live telemetry banner with per-key token quota monitor (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Storage & Disk Usage Monitor
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/status.html:173-214` |
| What it does | Tracks system disk usage metrics (total, used, free GB, percentage) with low disk space alerts (<10GB or >90%). Breaks down storage footprint across `raw/`, `outputs/`, and `temp/` directories. |
| Industrial-grade? | **A** - Proactive disk monitoring preventing ffmpeg rendering crashes due to full disks. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Storage gauge with directory usage breakdown (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: Manual Video Ingestion & Per-Video Runtime Tuning Accordion
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/status.html:215-515` |
| What it does | Manual pipeline submission form featuring dynamic pre-flight verification (debounced oEmbed lookup, duration checks, token estimation, AI tier recommendations, channel auto-detection), Force Re-run toggle, Dry Run switch, caption style preset dropdown, and a 3-column collapsible runtime overrides accordion (LLM model, temperature, duration bounds, clip count limits, Whisper model/language override, CRF, preset, scale algorithm, SFX switch, audio normalization). |
| Industrial-grade? | **A** - Comprehensive operator controls with preflight validation and granular per-job tuning. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (guards against duplicate triggers). **Input validation:** Pass (enforces numeric input bounds). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (YouTube oEmbed). **Security:** Pass (`csrf_token`). **Performance:** Pass (debounced preflight). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Preflight-gated ingestion form with per-video tuning accordion (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: Recent Ingestion & Pipeline Activity Table
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/status.html:516-894` |
| What it does | Displays recent processing jobs with 16:9 thumbnail previews, status pills, stage stepper bars, elapsed turnaround timers, duration, and clip yield counters. Features action buttons: Resume (for interrupted/stalled jobs), Cancel (for running jobs), 1-Click Retry (for failed jobs), Clean Re-Ingest (purging partial disk fragments), Force Re-run, and Dismiss (via universal soft delete modal). Includes an expandable child clip drawer. |
| Industrial-grade? | **A** - Complete pipeline supervision and recovery command center. |
| Checklist | **Error handling:** Pass (graceful thumbnail fallback). **Timeouts:** N/A. **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF protected forms). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Live activity table with multi-stage steppers and lifecycle action buttons (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: Client Scripts: SSE Telemetry, Interactive Filtering, Sorting & Drag-and-Drop
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/status.html:895-1761` |
| What it does | Full client application logic: 250ms debounced YouTube URL preflight check; client-side table search, filtering, and sorting; real-time Server-Sent Events listener updating counters, key metrics, and activity rows live without full page refreshes; and browser-wide drag-and-drop URL ingestion support. |
| Industrial-grade? | **A** - Highly responsive operational interface with zero-refresh live data synchronization. |
| Checklist | **Error handling:** Pass (handles SSE disconnects, JSON parse errors). **Timeouts:** N/A. **Retries:** Pass (EventSource auto-reconnect). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (DOM mutations scoped to changed rows). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: SSE-driven live DOM patching with client-side sorting and drag-drop (Recommended). |
| Priority / Effort | P3 / S |
