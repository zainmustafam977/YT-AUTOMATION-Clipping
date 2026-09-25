# Assessment Cards: shorts_engine/dashboard/routes/status.py

---

## Unit 1: Provider Health & Disk Storage Diagnostics
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:51-152` |
| What it does | `get_ai_provider_status` inspects configured LLM provider and fallback key pools (Groq, Gemini, OpenRouter, OpenAI, Heuristic fallback) to report active tier health. `get_storage_stats` queries disk space metrics (`shutil.disk_usage`) and calculates directory usage for `raw/`, `outputs/`, and `temp/` with low disk space alerts (< 10GB or > 90%). |
| Industrial-grade? | **A** - Resilient diagnostics with non-blocking directory stat scanning and fallback defaults. |
| Checklist | **Error handling:** Pass (gracefully catches disk usage and stat exceptions). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard disk usage and provider inspection (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Background Runner & Pipeline Stage Resolution
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:154-252` |
| What it does | `_run_pipeline_background` launches `PipelineRunner` in a background daemon thread with an isolated database session (`SessionLocal()`), clears stale cancellation flags, and commits/closes DB resources cleanly. `compute_pipeline_stages` maps video lifecycle status into visual progress stages (`ingest`, `transcribe`, `select`, `reframe`, `render`, `gate`). |
| Industrial-grade? | **A** - Thread isolation with dedicated session lifecycle and comprehensive stage status mapping. |
| Checklist | **Error handling:** Pass (isolated DB session with try/finally cleanup and logging). **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (explicit `db.close()`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dedicated thread runner with scoped DB sessions (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: build_recent_videos_payload (N+1 Free Video Hydration)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:254-550` |
| What it does | Assembles rich telemetry for recent source videos in exactly 2 batched SQL queries (joined loading channel, transcript, candidates, and clips), eliminating N+1 overhead. Groups candidates in memory, correlates live progress from `job_progress_tracker`, detects stale/interrupted locks (>600s), and computes elapsed turnaround times. |
| Industrial-grade? | **A** - Highly optimized query structure preventing database connection starvation under dashboard polling. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass (non-blocking lock checking for stale detection). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (strictly 2 SQL queries regardless of video count). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Two-query eager loaded hydration with in-memory candidate grouping (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: GET / & GET /api/status (Status Dashboard UI & JSON APIs)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:552-663, 1533-1557` |
| What it does | Serves the main status dashboard template (`status.html`) and programmatic status JSON API (`/api/status`). Injects quota metrics, active AI provider status, per-key token metrics, storage metrics, and CSRF token. |
| Industrial-grade? | **A** - High usability, clean permission gating (`require_viewer`), and complete operational visibility. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_viewer`, CSRF token injected). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard template and JSON status rendering (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: POST /process-video (Manual Video Ingestion Trigger)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:664-866` |
| What it does | Accepts manual video URL or 11-char ID, parses parameters, performs oEmbed channel auto-discovery if unregistered, validates against concurrent locks and duplicate active processing, supports `force` re-run (purging prior candidates and resetting state to `DETECTED`), extracts runtime tuning overrides, and spawns the background worker. |
| Industrial-grade? | **A** - Comprehensive pre-flight protection against duplicate runs and race conditions. |
| Checklist | **Error handling:** Pass (validates URL regex, handles oEmbed network timeouts gracefully). **Timeouts:** Pass (2.5s oEmbed timeout). **Retries:** N/A. **Idempotency:** Pass (guards against double-triggering active locks). **Input validation:** Pass (strict regex extraction). **Resource cleanup:** Pass. **Concurrency safety:** Pass (`get_video_lock` concurrency gating). **Atomicity:** Pass. **External-dependency:** Pass (YouTube oEmbed). **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (operator audit logs). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Lock-gated ingestion trigger with oEmbed channel auto-registration (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: Video Lifecycle Controls (Cancel, Retry, Resume, Clean-Retry, Delete)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:868-1167` |
| What it does | Operational endpoints for video pipeline control: `cancel_video_processing` releases locks and marks `FAILED`; `retry_video_processing` increments retry counter; `resume_video_processing` reconciles stale claims and resumes from last stage checkpoint; `clean_retry_video_processing` sweeps disk for stale temp fragments and resets DB state; `delete_source_video` dismisses video records with optional cascading disk wipe (`delete_files=True`). |
| Industrial-grade? | **A** - Full operational flexibility with safe resource unlinking and state machine transition guards. |
| Checklist | **Error handling:** Pass (guards against invalid status transitions with HTTP 409). **Timeouts:** N/A. **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (safe disk unlinking via `PathManager.safe_unlink`). **Concurrency safety:** Pass (explicit lock acquisition checks). **Atomicity:** Pass (DB transactions). **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (operator actions logged). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Granular lifecycle endpoints with disk fragment pruning (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 7: Thumbnail Extraction & SVG Fallback Resolver
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:1169-1294` |
| What it does | Serves `/api/thumbnails/{id}`: checks local cached clip thumbnails, extracts 1-second frames via ffmpeg if video exists, redirects to YouTube's CDN for 11-char IDs, or renders an SVG placeholder if unavailable. |
| Industrial-grade? | **A** - Zero broken image icons guaranteed across dashboard views. |
| Checklist | **Error handling:** Pass (ffmpeg errors caught gracefully, falls back to SVG). **Timeouts:** Pass (ffmpeg timeout 5s). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (ffmpeg / YouTube CDN). **Security:** Pass. **Performance:** Pass (cached thumbnails served with `Cache-Control: public, max-age=86400`). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Multi-tiered thumbnail resolution with SVG vector fallback (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 8: GET /api/events/dashboard (Real-Time SSE Stream)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:1296-1377` |
| What it does | Server-Sent Events stream emitting live JSON snapshots every 2 seconds. Critically uses short-lived scoped database sessions (`with SessionLocal() as db:`) to query counters, quotas, and recent video states, releasing DB connections back to the pool immediately between ticks. |
| Industrial-grade? | **A** - Pool-safe SSE implementation preventing SQLite/Postgres connection starvation. |
| Checklist | **Error handling:** Pass (handles client disconnection). **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (connection released every tick). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_viewer`). **Performance:** Pass (short scoped DB query, sleep 2s). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Scoped short-lived session SSE generator (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 9: Pre-Flight Verification & Analytics Sync
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/status.py:1379-1531, 1559-1584` |
| What it does | `check_video_status` provides instant pre-flight checks returning video metadata, token estimates, tier recommendations, and channel detection via YouTube oEmbed. `sync_youtube_analytics` syncs YouTube view metrics for published clips and automatically retrains the ML virality predictor. |
| Industrial-grade? | **A** - High-value operator feedback loops with dynamic machine learning model retraining. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (oEmbed 2.5s timeout). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_operator` for sync, `require_viewer` for check). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pre-flight metadata extraction + automated analytics retraining (Recommended). |
| Priority / Effort | P3 / S |
