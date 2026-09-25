# Assessment Cards: shorts_engine/services/captioning/sfx_mapper.py

---

## Unit 1: KEYWORD_SFX_MAP & SoundCue Class
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/sfx_mapper.py:19-59` |
| What it does | Keyword taxonomy mapping dialogue triggers across 8 commercial audio categories (`whoosh`, `impact`, `ding`, `pop`, `camera_click`, `glitch`, `sub_bass_drop`, `riser`) and data model encapsulating temporal offsets and trigger words. |
| Industrial-grade? | **A** - Well-curated dictionary of high-engagement conversational triggers. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (immutable map). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (`__repr__`). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Curated dictionary mapping (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Procedural Audio Synthesizer (generate_synthetic_sfx & resolve_sfx_path)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/sfx_mapper.py:61-130` |
| What it does | Synthesizes 16-bit 44.1kHz PCM WAV files in memory using pure standard library (`wave`, `struct`, `math`) with category-specific frequency envelopes and exponential decay; resolves physical sound assets and automatically falls back to synthesis if assets are missing on disk. |
| Industrial-grade? | **A** - Zero-crash fallback architecture: missing sound effects trigger loud alerts but procedurally generate valid audio to guarantee FFmpeg renders never fail. |
| Checklist | **Error handling:** Pass (catches missing files, produces fallback). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (produces deterministic synthetic WAVs). **Input validation:** Pass. **Resource cleanup:** Pass (`with wave.open(...)`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (stdlib only). **Security:** Pass. **Performance:** Pass (fast algorithmic sample generation). **Observability:** Pass (loud alert count and warning logged). **Config:** Pass (`settings.TEMP_DIR`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Procedural wave synthesis with loud alerting (Recommended - guarantees 100% unattended render success). Option 2: Hard crash on missing WAV (Amateur). |
| Priority / Effort | P3 / S |

---

## Unit 3: SFXMapper Class & map_sfx_cues Method
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/sfx_mapper.py:132-163` |
| What it does | Scans word timestamps for emotional trigger words and attaches audio cue tags, enforcing a strict minimum spacing interval (4.0s default) to prevent sound cue clutter. |
| Industrial-grade? | **A** - Temporal spacing prevents overwhelming the viewer with overlapping sound effects. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (cleans punctuation, handles empty word list). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass (`min_interval_sec`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Temporal spacing interval filter (Recommended). Option 2: Unfiltered trigger mapping (Results in audio clutter). |
| Priority / Effort | P3 / S |
