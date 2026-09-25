# Assessment Cards: shorts_engine/services/rendering/compositor.py

---

## Unit 1: escape_ffmpeg_filter_path
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/compositor.py:20-32` |
| What it does | Escapes a filesystem path for inclusion in FFmpeg filter graph parameters by resolving absolute path, replacing backslashes with forward slashes, escaping Windows drive colons (`D\:/`), and escaping single quotes. |
| Industrial-grade? | **A** - Robust cross-platform path sanitizer preventing FFmpeg filtergraph syntax parsing errors. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (prevents filter injection). **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current escaping logic (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: get_default_fontfile
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/compositor.py:34-47` |
| What it does | Scans standard system font paths on Windows, Linux, and macOS to locate a reliable TTF/OTF font file for FFmpeg `drawtext` filters. |
| Industrial-grade? | **B** - Practical fallback scan, but returns `None` on minimal/distroless Docker containers lacking standard fonts. |
| Checklist | **Error handling:** Pass (returns None if not found). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If `None` is returned in a containerized Linux environment without DejaVu fonts, FFmpeg relies on `fontconfig` which will error or abort if no default font matches. |
| Improvement | Bundle a fallback font (e.g. `assets/fonts/Inter-Bold.ttf`) in the repository or Docker image and check it first in candidate paths. |
| Better approach? | Option 1: Repository-bundled fallback font (Recommended). Option 2: System-only font paths (Current). |
| Priority / Effort | P2 / S |

---

## Unit 3: RenderValidationResult
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/compositor.py:50-85` |
| What it does | Encapsulates post-render validation metrics including duration, dimensions (width, height), audio presence, file size, validity flag, and error diagnostics with serialization methods. |
| Industrial-grade? | **A** - Clean, strongly typed immutable data container. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (`to_dict` and `__repr__`). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current dataclass/class model (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: _test_hardware_encoder & get_best_video_encoder
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/compositor.py:90-138` |
| What it does | Probes available hardware acceleration encoders (`h264_nvenc`, `h264_qsv`, `h264_amf`) with a fast 1-frame probe test (`lavfi testsrc`) before assigning flags. Caches the verified encoder or falls back cleanly to software `libx264`. |
| Industrial-grade? | **A-** - Excellent pre-flight hardware probe that avoids hard crashes on systems with broken or unavailable GPU drivers. |
| Checklist | **Error handling:** Pass (catches probe exceptions and falls back). **Timeouts:** Pass (2.0s probe timeout). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass (cache mutation is idempotent). **Atomicity:** Pass. **External-dependency:** Pass (FFmpeg). **Security:** Pass. **Performance:** Pass (fast 0.04s synthetic frame). **Observability:** Pass (logs selected encoder). **Config:** Pass (reads CRF and preset). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Global `_CACHED_ENCODER` is not protected by a threading Lock during simultaneous initialization in multi-threaded workers. |
| Improvement | Add a simple `threading.Lock` around `_CACHED_ENCODER` lookup and assignment. |
| Better approach? | Option 1: Thread-safe singleton lock (Recommended). Option 2: Unlocked global (Current). |
| Priority / Effort | P3 / S |

---

## Unit 5: VideoCompositor.render_short
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/compositor.py:145-426` |
| What it does | Assembles complete 9:16 portrait Short video: scales and crops according to active-speaker crop plan, burns karaoke ASS subtitles, burns opening hook banner overlay (0-3s), mixes B-roll stock footage overlays, ducks background music under dialogue via sidechain compression, and executes FFmpeg with `SubprocessWatchdog`. Automatically fails over from GPU to CPU `libx264` if hardware encoding fails mid-render. |
| Industrial-grade? | **A** - Industrial-grade video composition pipeline with supervised watchdog execution, dynamic timeout scaling (`max(180.0, duration * 6)`), automatic GPU-to-CPU failover, guaranteed temp-file cleanup, and web-optimized atomicity (`+faststart`). |
| Checklist | **Error handling:** Pass (supervised exit codes, automatic CPU fallback, detailed error logs). **Timeouts:** Pass (enforces proportional timeout via `SubprocessWatchdog`). **Retries:** Pass (automatic 1x retry on CPU if hardware encoder fails). **Idempotency:** Pass (`-y` flag). **Input validation:** Pass. **Resource cleanup:** Pass (`finally` block unlinks temp hook textfile). **Concurrency safety:** Pass. **Atomicity:** Pass (cleans up corrupted output artifact on timeout or abort). **External-dependency:** Pass (FFmpeg). **Security:** Pass (arg lists, escaped filter parameters). **Performance:** Pass (hardware acceleration with fastseek `-ss` before `-i`). **Observability:** Pass (detailed log of render parameters and timeout). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None of significance. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current supervised watchdog + auto-fallback architecture (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: VideoCompositor.validate_render
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/compositor.py:428-501` |
| What it does | Inspects the finished MP4 file with `ffprobe` to verify 9:16 aspect ratio (1080x1920), duration > 0, presence of valid audio stream, and non-empty file size. |
| Industrial-grade? | **B+** - Rigorous technical validation, but uses `subprocess.run` without an explicit timeout parameter. |
| Checklist | **Error handling:** Pass (handles non-zero returncode, JSON parse errors, missing files). **Timeouts:** Fail (no timeout specified on `subprocess.run`). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (checks file existence and non-zero size). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`ffprobe`). **Security:** Pass (list args). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If `ffprobe` encounters a corrupted container with invalid atom offsets, it can hang waiting on I/O indefinitely without a timeout. |
| Improvement | Add `timeout=15.0` to the `subprocess.run(["ffprobe", ...])` invocation. |
| Better approach? | Option 1: `timeout=15.0` or `SubprocessWatchdog.run` (Recommended). Option 2: Unbounded `subprocess.run` (Current - hang risk). |
| Priority / Effort | P1 / S |
