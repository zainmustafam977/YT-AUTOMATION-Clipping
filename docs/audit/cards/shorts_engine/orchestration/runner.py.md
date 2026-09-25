# Assessment Cards: shorts_engine/orchestration/runner.py

---

## Unit 1: Cooperative Cancellation & Per-Video Mutex Locks
| Field | Content |
|---|---|
| Location | `shorts_engine/orchestration/runner.py:45-102` |
| What it does | Provides thread-safe process-wide video locking (`get_video_lock`, `release_video_lock`) to prevent concurrent pipeline duplicate processing on the same video, and cooperative cancellation flags (`register_cancellation`, `is_video_cancelled`, `clear_cancellation`) allowing operators to abort active background tasks. |
| Industrial-grade? | **A** - Clean synchronization primitives with thread-safe mutex protection. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass (non-blocking lock acquisition). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (`finally` block releases lock). **Concurrency safety:** Pass (protected by `_VIDEO_LOCK_MUTEX` and `_CANCELLATION_MUTEX`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Locks are held in-memory within the active Python process. In a distributed multi-node deployment, an external distributed lock (e.g. Redis Redlock or Postgres advisory lock) is required. |
| Improvement | Support optional Redis-backed distributed lock for multi-container deployments. |
| Better approach? | Option 1: Redis / DB advisory lock for clusters (Recommended). Option 2: In-memory threading lock (Current - single host). |
| Priority / Effort | P2 / M |

---

## Unit 2: PipelineRunner.run_pipeline_for_video
| Field | Content |
|---|---|
| Location | `shorts_engine/orchestration/runner.py:136-751` |
| What it does | Master orchestration coordinator: executes Step A (Ingestion & creator auto-detection), Step B (Transcription), Step C (LLM clip selection), and Step D (Reframing, Subtitle burning, SFX mapping, Compositing, Thumbnail generation, Media QA & Multimodal Quality Gate). |
| Industrial-grade? | **B** - Industrial-grade multi-stage pipeline, but contains a critical `NoneType` bug on resumed runs. |
| Checklist | **Error handling:** Pass (graceful error capture, DB status updates, alert dispatch). **Timeouts:** Pass. **Retries:** Pass. **Idempotency:** Pass (skips already completed stages). **Input validation:** Pass (extracts clean video ID). **Resource cleanup:** Pass (lock released in `finally`). **Concurrency safety:** Pass (claims clip via `claim_clip_by_id`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (real-time telemetry and stage percentages). **Config:** Pass (reads runtime overrides). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | **Critical Defect (Resumed Pipeline Crash):** On line 679: `seg_text = " ".join([w.word for w in formatted_transcript.words ...])`. If a pipeline is resumed from `SEGMENTS_SELECTED`, Step B was skipped, leaving `formatted_transcript = None`. Line 679 will raise `AttributeError: 'NoneType' object has no attribute 'words'` and abort the pipeline! |
| Improvement | If `formatted_transcript` is None, reconstruct `words` by parsing `video.transcript.word_level_json` from the database. |
| Better approach? | Option 1: DB transcript reconstruction fallback (Recommended - bug fix). Option 2: Unchecked attribute access (Current - crashes on resume). |
| Priority / Effort | P0 / S |

---

## Unit 3: PipelineRunner.re_evaluate_clips_for_video & reconcile_default_channel_videos
| Field | Content |
|---|---|
| Location | `shorts_engine/orchestration/runner.py:766-898` |
| What it does | `re_evaluate_clips_for_video` executes in-place Quality Gate re-evaluations for existing rendered clips using updated duration bounds and soft advisory rules to recover valid clips without re-rendering. `reconcile_default_channel_videos` scans orphan videos assigned to `default_channel` and maps them to their creator channel using YouTube oEmbed or title matching. |
| Industrial-grade? | **A** - High-value operational utilities for fleet recovery and channel hygiene. |
| Checklist | **Error handling:** Pass (catches oEmbed connection errors). **Timeouts:** Pass (3.0s timeout on oEmbed HTTP). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (httpx client context manager). **Concurrency safety:** Pass. **Atomicity:** Pass (commits reassigned channels). **External-dependency:** Pass (YouTube oEmbed API). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (detailed recovery counts and logs). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current oEmbed + title reconciliation (Recommended). |
| Priority / Effort | P3 / S |
