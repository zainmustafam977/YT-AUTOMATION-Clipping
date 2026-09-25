# Assessment Cards: shorts_engine/services/rendering/thumbnail_generator.py

---

## Unit 1: class_or_instance_method Decorator
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/thumbnail_generator.py:14-27` |
| What it does | Descriptor decorator enabling methods to be invoked either statically on the class (`ThumbnailGenerator.generate_thumbnail(...)`) or on an instance (`gen.generate_thumbnail(...)`). |
| Industrial-grade? | **A** - Clean Python descriptor pattern providing ergonomic call ergonomics. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Descriptor decorator (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: ThumbnailGenerator.extract_frame_ffmpeg
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/thumbnail_generator.py:36-58` |
| What it does | Extracts a single video frame at specified timestamp using FFmpeg (`-ss`, `-vframes 1`, `-q:v 2`). |
| Industrial-grade? | **B** - High-quality frame extraction, but uses unbounded `subprocess.run` without timeout supervision. |
| Checklist | **Error handling:** Pass (catches exceptions, returns boolean). **Timeouts:** Fail (no timeout on `subprocess.run`). **Retries:** N/A. **Idempotency:** Pass (`-y` flag). **Input validation:** Pass (clamps minimum timestamp). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (FFmpeg). **Security:** Pass (arg list prevents shell injection). **Performance:** Pass (fast input seeking with `-ss` before `-i`). **Observability:** Pass (logs warning on failure). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If FFmpeg encounters an unindexed or corrupted container, `subprocess.run` can hang indefinitely without a timeout. |
| Improvement | Use `SubprocessWatchdog.run(cmd, timeout_sec=20.0)` for supervised execution. |
| Better approach? | Option 1: Supervised watchdog execution (Recommended). Option 2: Bare `subprocess.run` (Current - hang risk). |
| Priority / Effort | P1 / S |

---

## Unit 3: ThumbnailGenerator.generate_thumbnail
| Field | Content |
|---|---|
| Location | `shorts_engine/services/rendering/thumbnail_generator.py:60-184` |
| What it does | Generates 1080x1920 YouTube Shorts cover images: applies 15% color/contrast saturation boost, paints top/bottom safe-zone gradient scrims to prevent YouTube UI overlay collisions, typesets 3-line hook text with 8-direction drop shadows, and encodes optimized JPEG (quality=92) with guaranteed temp frame cleanup. |
| Industrial-grade? | **A-** - High-CTR automated cover generation with gradient safe-zone protection and guaranteed `finally` cleanup. |
| Checklist | **Error handling:** Pass (retries at 0.5s if primary frame extraction fails; wraps in try/finally). **Timeouts:** Pass. **Retries:** Pass (frame fallback). **Idempotency:** Pass. **Input validation:** Pass (checks video existence). **Resource cleanup:** Pass (guaranteed temp frame unlinking in finally). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Pillow). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Font resolution scans hardcoded OS font directories (`C:/Windows/Fonts`, `/usr/share/fonts`). On stripped Docker base images without fonts, falls back to `ImageFont.load_default()` which renders tiny unreadable text on a 1080x1920 canvas. |
| Improvement | Bundle a standard open-source bold font (e.g. Montserrat or Impact alternative) in `assets/fonts/` as reliable fallback. |
| Better approach? | Option 1: Bundled repository font fallback (Recommended for reproducible container builds). Option 2: OS font scan with default bitmap fallback (Current). |
| Priority / Effort | P2 / S |
