# Assessment Cards: shorts_engine/services/transcription/engine.py

---

## Unit 1: TranscriptionService.__init__ & Hardware Auto-Detection
| Field | Content |
|---|---|
| Location | `shorts_engine/services/transcription/engine.py:18-45` |
| What it does | Configures ASR runtime parameters, auto-detects CUDA GPU vs CPU availability, assigns optimal quantization (`float16` for CUDA, `int8` for CPU), and sets up forced alignment. |
| Industrial-grade? | **A** - Zero-configuration hardware adaptation enabling portable execution on developer laptops and GPU servers. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`torch`, `faster_whisper`). **Security:** Pass. **Performance:** Pass (int8 CPU quantization). **Observability:** Pass (logs hardware configuration). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dynamic hardware detection with int8 fallback (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: TranscriptionService.model (Lazy Loading)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/transcription/engine.py:47-60` |
| What it does | Lazy-initializes CTranslate2 `faster_whisper.WhisperModel` on first access, downloading model weights to persistent `settings.MODELS_DIR/whisper`. |
| Industrial-grade? | **A** - Lazy instantiation prevents heavy neural weights from consuming memory if cloud Groq Whisper handles transcription. |
| Checklist | **Error handling:** Pass (catches model download/initialization errors, raises RuntimeError). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Warning (multiple threads accessing property simultaneously could double-instantiate). **Atomicity:** Pass. **External-dependency:** Pass (`faster_whisper`). **Security:** Pass. **Performance:** Pass (lazy load). **Observability:** Pass (logs model loading errors). **Config:** Pass (`settings.MODELS_DIR`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If two worker threads access `self.model` simultaneously on cold start, both could attempt to load the CTranslate2 model into VRAM at the same time. |
| Improvement | Protect `_model` initialization with a `threading.Lock()`. |
| Better approach? | Option 1: Double-checked locking with `threading.Lock` (Recommended for multi-threaded workers). Option 2: Unsynchronized lazy property (Current). |
| Priority / Effort | P2 / S |

---

## Unit 3: TranscriptionService._transcribe_with_groq
| Field | Content |
|---|---|
| Location | `shorts_engine/services/transcription/engine.py:62-127` |
| What it does | Offloads speech-to-text to Groq Cloud Whisper API (`whisper-large-v3-turbo`), returning word-level timestamped segments in ~2 seconds with transparent local fallback on failure. |
| Industrial-grade? | **A** - High-speed zero-cost transcription (28.8k audio seconds daily free tier quota) with seamless fallback. |
| Checklist | **Error handling:** Pass (catches all HTTP and JSON errors, returns None to trigger local fallback). **Timeouts:** Pass (60s httpx timeout). **Retries:** N/A (falls back immediately). **Idempotency:** Pass. **Input validation:** Pass (checks API key presence). **Resource cleanup:** Pass (`with httpx.Client(...)`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Groq API, `httpx`). **Security:** Pass (transmits key in Authorization header). **Performance:** Pass (~2s vs 45s on local CPU). **Observability:** Pass (logs status codes and word counts). **Config:** Pass (`settings.get_groq_keys()`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Groq API enforces a 25 MB payload limit. Raw 16kHz WAV files for audio longer than ~15 minutes can exceed 25 MB and fail with HTTP 413, falling back to slower local CPU. |
| Improvement | Compress audio to 64k mono MP3 or Opus before sending to Groq if file size exceeds 20 MB. |
| Better approach? | Option 1: Compressed MP3 buffer upload (Recommended - keeps 2-hour podcasts under 25 MB). Option 2: Raw file upload with local fallback (Current). |
| Priority / Effort | P2 / S |

---

## Unit 4: TranscriptionService.transcribe
| Field | Content |
|---|---|
| Location | `shorts_engine/services/transcription/engine.py:128-223` |
| What it does | Coordinates end-to-end transcription: attempts cloud Groq Whisper first, falls back to local `faster-whisper` with Silero VAD (400ms pause filter), streams real-time progress to telemetry tracker, and applies monotonic timestamp alignment. |
| Industrial-grade? | **A** - Enterprise-grade resilience combining cloud speed, local fallback, VAD noise rejection, and live progress reporting. |
| Checklist | **Error handling:** Pass (validates file existence, handles empty segments). **Timeouts:** Pass. **Retries:** Pass (cloud -> local fallback). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (VAD skips silent audio frames). **Observability:** Pass (granular progress reporting). **Config:** Pass (`settings.USE_GROQ_WHISPER`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dual cloud/local pipeline with VAD and alignment (Recommended). |
| Priority / Effort | P3 / S |
