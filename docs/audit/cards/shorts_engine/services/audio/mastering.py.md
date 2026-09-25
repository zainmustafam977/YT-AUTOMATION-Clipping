# Assessment Cards: shorts_engine/services/audio/mastering.py

---

## Unit 1: AudioMasteringEngine.__init__
| Field | Content |
|---|---|
| Location | `shorts_engine/services/audio/mastering.py:31-44` |
| What it does | Initializes audio engineering parameters for speech cleaning, sidechain ducking, and EBU R128 broadcast loudness compliance (-14 LUFS target for YouTube Shorts, -1.0 dBFS True Peak ceiling, 80Hz 2-pole HPF). |
| Industrial-grade? | **A** - Calibrated to YouTube and TikTok mobile platform playback specifications. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass (falls back to `settings`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Parameterized defaults matching YouTube Shorts platform targets (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: build_speech_clean_filter & build_single_stream_master_filter
| Field | Content |
|---|---|
| Location | `shorts_engine/services/audio/mastering.py:45-54, 97-103` |
| What it does | Constructs FFmpeg audio filter chains stripping low-frequency HVAC rumble (80Hz highpass) and room noise floor (`agate` -36dB threshold), followed by single-stream loudness normalization. |
| Industrial-grade? | **A** - Studio-grade acoustic filter graph optimized for voice clarity on phone speakers. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (FFmpeg). **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Native FFmpeg `highpass` + `agate` + `loudnorm` (Recommended - fast and zero external Python dependencies). |
| Priority / Effort | P3 / S |

---

## Unit 3: build_sidechain_ducking_filter_graph
| Field | Content |
|---|---|
| Location | `shorts_engine/services/audio/mastering.py:55-95` |
| What it does | Constructs complex FFmpeg filtergraph splitting clean dialogue into broadcast feed and sidechain control trigger, dynamically attenuating the background music bed by -14dB on vocal onset (15ms attack, 350ms release), mixing via `amix`, and mastering through EBU R128 `loudnorm`. |
| Industrial-grade? | **A** - Professional radio broadcast ducking architecture executed purely within FFmpeg's C engine without decoding intermediate PCM streams into Python. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (FFmpeg). **Security:** Pass. **Performance:** Pass (zero memory copy between Python and FFmpeg). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: FFmpeg internal sidechaincompress + loudnorm (Recommended - highest performance and precision). Option 2: Python numpy/scipy envelope follower (Too slow; high memory overhead). |
| Priority / Effort | P3 / S |

---

## Unit 4: measure_loudness
| Field | Content |
|---|---|
| Location | `shorts_engine/services/audio/mastering.py:104-136` |
| What it does | Runs a first-pass null-render loudnorm measurement via FFmpeg and parses stderr JSON to extract integrated loudness (LUFS), True Peak (dBFS), and Loudness Range (LU). |
| Industrial-grade? | **B** - Accurately extracts ITU-R BS.1770 metrics from stderr JSON, but uses bare `subprocess.run` without timeout protection. |
| Checklist | **Error handling:** Pass (defensive JSON extraction with rfind brackets). **Timeouts:** Fail (unbounded `subprocess.run`). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (FFmpeg). **Security:** Pass (arg list prevents shell injection). **Performance:** Pass (renders to null sink). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If FFmpeg hangs on a malformed or corrupted audio container, `subprocess.run` blocks indefinitely without a timeout. |
| Improvement | Replace `subprocess.run` with `SubprocessWatchdog.run(..., timeout_sec=60.0)`. |
| Better approach? | Option 1: Supervise via `SubprocessWatchdog.run` with 60s timeout (Recommended). Option 2: Bare `subprocess.run` (Current - hang risk). |
| Priority / Effort | P1 / S |

---

## Unit 5: master_audio
| Field | Content |
|---|---|
| Location | `shorts_engine/services/audio/mastering.py:137-192` |
| What it does | Coordinates full audio mastering pipeline: builds filter graph, loops music track if provided, encodes to AAC 320kbps at 48kHz, and supervises FFmpeg execution via `SubprocessWatchdog` with automated cleanup on failure. |
| Industrial-grade? | **A** - Supervised subprocess execution with dynamic timeout budgeting and automated cleanup of partial outputs. |
| Checklist | **Error handling:** Pass (raises RuntimeError on non-zero exit code). **Timeouts:** Pass (watchdog with dynamic timeout). **Retries:** N/A. **Idempotency:** Pass (`-y` flag). **Input validation:** Pass (checks music track existence). **Resource cleanup:** Pass (`artifacts_to_cleanup=[output_path]`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (FFmpeg). **Security:** Pass (vectorized command args). **Performance:** Pass (AAC 320k high fidelity). **Observability:** Pass. **Config:** Pass (`settings.AUDIO_BITRATE`, `AUDIO_SAMPLE_RATE`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |
