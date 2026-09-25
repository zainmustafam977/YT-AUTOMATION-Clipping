# Assessment Cards: shorts_engine/dashboard/routes/queue.py

---

## Unit 1: GET /queue (Review Queue Listing, Multi-Facet Search & Grouping)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/queue.py:1-323` |
| What it does | Serves the interactive review queue dashboard (`/queue`) supporting Active and Rejected clip tabs. Provides multi-facet filtering (keyword search across hook/payoff/source title/video ID, channel ID, source video ID, AI provider/failover/heuristic provenance), sorting (score, duration, date), and pagination. Enriches candidate clips with ML virality predictions, parsed Quality Gate assessment badges (technical, framing, semantic, advisory), and groups clips hierarchically by source video and date. |
| Industrial-grade? | **A** - Rich operational interface with high diagnostic fidelity and responsive filtering. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (clamps pagination bounds). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_viewer`, CSRF token passed). **Performance:** Pass (paged SQL joins). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Paged SQL outerjoin with in-memory hierarchical grouping (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: GET /queue/export & GET /queue/{clip_id}/video
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/queue.py:324-423` |
| What it does | `/queue/export` downloads filtered clip metadata in CSV or JSON format for publishing pipelines or audits. `/queue/{clip_id}/video` streams rendered MP4 short files with proper content types (`video/mp4`) and 404 validation. |
| Industrial-grade? | **A** - Clean metadata export and video file streaming with validation. |
| Checklist | **Error handling:** Pass (HTTP 404 if clip or rendered file missing). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_viewer`). **Performance:** Pass (streaming FileResponse). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Direct FileResponse streaming + in-memory CSV/JSON builder (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: Clip Review Actions (Approve, Undo, Schedule, A/B Variations)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/queue.py:424-600` |
| What it does | Operators can approve clips (`/approve`), undo decisions (`/undo`), schedule future publishing (`/schedule` with ISO-8601 validation), and choose A/B title/hook variations (`/select-variation` with curiosity gap, contrarian, or high stakes angles). Supports HTMX toasts and form redirects. |
| Industrial-grade? | **A** - Frictionless review workflow with undo safety and schedule validation. |
| Checklist | **Error handling:** Pass (validates status transitions with HTTP 409, invalid timestamps with HTTP 400). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (`datetime.fromisoformat` validation). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass (DB transactions). **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (operator action audit trails). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: HTMX interactive actions with toast triggers and undo support (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: Rejection & Quality Gate Recovery Diagnostics
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/queue.py:601-848` |
| What it does | Full lifecycle for rejected clips: operators can reject clips with custom reasons (`/reject`), restore clips to active queue (`/restore`), open native local explorer folders (`/open-folder`), fetch detailed Quality Gate failure diagnostics and suggested fixes (`/diagnostics`), re-evaluate single clips or entire source videos (`/re-evaluate`), force-approve clips with operator audit notes (`/force-approve`), apply automated repairs (`/fix` for duration clamp, caption margin shifting, transcript re-anchoring), and execute bulk batch approvals/rejections (`/batch-action`). |
| Industrial-grade? | **A** - Industry-standard recovery and diagnostic toolset empowering operators to inspect and fix quality check rejections. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (operator actions logged). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dedicated ClipRecoveryService delegation for diagnostics and repairs (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: In-Place Editing (Caption Style Re-Render & Word-Snap Trimming)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/queue.py:850-1049` |
| What it does | `/queue/{clip_id}/re-render-style` re-renders a clip with one of 5 presets (Alex Hormozi, MrBeast, Cyberpunk Neon, Minimalist, High Contrast Box) or custom typography (font, colors, outline width, margins, max words per line, casing), regenerating ASS subtitles and compositing video. `/queue/{clip_id}/trim` dynamically adjusts start/end timestamps with speech pause snapping via `ClipTrimmerService`. |
| Industrial-grade? | **A** - High creative flexibility allowing operators to polish captions and pacing in-place. |
| Checklist | **Error handling:** Pass (guards against missing downloads or invalid templates). **Timeouts:** Pass (uses rendering timeouts). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (parses styles and numeric bounds). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (ffmpeg). **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Modular ASS subtitle regeneration with ffmpeg compositing (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: Clip Deletion & Dead-Letter Lifecycle
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/queue.py:1050-1318` |
| What it does | Provides full lifecycle deletion and recovery endpoints: `/queue/{clip_id}/delete`, `/dead-letter`, `/dead-letter/clip/{id}/retry`, `/dead-letter/video/{id}/retry` (bounded by `MAX_RETRY_COUNT`), individual failure dismissal `/dead-letter/clip/{id}/delete` and `/dead-letter/video/{id}/delete`, and bulk purging `/dead-letter/clear-all` supporting selective media file unlinking (`delete_files=True`) and process lock / cancellation cleanup. |
| Industrial-grade? | **A** - Comprehensive dead-letter administration with safe cascading cleanup and retry limits. |
| Checklist | **Error handling:** Pass (HTTP 422 when retry limit exceeded, 404 for missing items). **Timeouts:** N/A. **Retries:** Pass (bounded by `MAX_RETRY_COUNT`). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (safe file unlinking via `PathManager.safe_unlink` and lock release). **Concurrency safety:** Pass. **Atomicity:** Pass (DB transactions). **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (audit logging on dismissal/retries). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dead-letter queue with retry throttling, safe file unlinking, and bulk purge (Recommended). |
| Priority / Effort | P3 / S |

