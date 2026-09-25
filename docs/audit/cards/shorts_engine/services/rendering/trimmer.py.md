# Assessment Cards: shorts_engine/services/rendering/trimmer.py

---

## Unit 1: snap_to_word_boundary
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/trimmer.py:30-80` |
| What it does | Evaluates word-level timestamps and snaps user-requested trim start and end points to natural inter-word silence pauses to prevent clipped vocal syllables. |
| Industrial-grade? | **B+** - Accurate syllable protection logic with duration safety guards, but contains an unused function parameter. |
| Checklist | **Error handling:** Pass (falls back to raw timestamps if list is empty or duration guard triggers). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure function). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (fast in-memory list comprehension). **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Parameter `min_silence_gap_sec` (line 35) is declared in the signature with a default of 0.20s, but is never referenced or enforced in the boundary search logic. |
| Improvement | Either enforce `min_silence_gap_sec` when evaluating silence intervals between adjacent words, or remove the unused parameter from the signature. |
| Better approach? | Option 1: Actively enforce silence gap threshold in boundary selection (Recommended). Option 2: Remove dead parameter. |
| Priority / Effort | P3 / S |

---

## Unit 2: ClipTrimmerService.__init__
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/trimmer.py:82-90` |
| What it does | Initializes the trimmer service with database session and downstream rendering/reframing/captioning dependencies (`VideoReframer`, `VideoCompositor`, `ASSSubtitleGenerator`). |
| Industrial-grade? | **A** - Clean dependency composition. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Direct instantiation couples the service to concrete implementations rather than injectable interfaces. |
| Improvement | Allow passing optional mock/override instances in `__init__` for unit testing without full FFmpeg and OpenCV binaries. |
| Better approach? | Option 1: Optional dependency injection with defaults (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: ClipTrimmerService.trim_clip
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/trimmer.py:91-260` |
| What it does | Performs sub-frame trimming and atomic re-rendering: parses word timestamps, applies syllable snapping, re-generates ASS subtitles with style presets, re-computes 9:16 crop plan, renders short video, inspects container integrity via `TechnicalVerifier`, and updates DB record atomically. |
| Industrial-grade? | **B+** - Robust end-to-end re-trim workflow that avoids re-downloading media, but lacks previous file cleanup and row-level locking. |
| Checklist | **Error handling:** Pass (validates clip, candidate, source media existence, catches JSON decode errors). **Timeouts:** Pass (delegates to compositor). **Retries:** N/A. **Idempotency:** Pass (overwrites or generates deterministic output path). **Input validation:** Pass (checks minimum 10s and maximum duration constraints). **Resource cleanup:** Needs improvement (superseded rendered MP4 is not unlinked or archived). **Concurrency safety:** Fail (lacks `.with_for_update()` row locking on concurrent trims). **Atomicity:** Pass (DB commit in single transaction). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (fast seeking and local file re-use). **Observability:** Pass (structured logs and QC metadata update). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | 1. If a user trims a clip repeatedly, previous rendered files on disk remain orphaned, consuming storage. 2. Concurrent HTTP requests to trim the same clip can run simultaneous FFmpeg render jobs on the same output path without mutex or DB row locking. |
| Improvement | 1. Acquire DB lock (`with_for_update()`) on clip retrieval. 2. Safely unlink or archive previous `clip.rendered_path` upon successful render of new version. |
| Better approach? | Option 1: Row locking + previous asset cleanup (Recommended). Option 2: Blind overwriting (Current). |
| Priority / Effort | P2 / M |
