# Assessment Cards: shorts_engine/services/ingestion/downloader.py

---

## Unit 1: VideoDownloadResult Class
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ingestion/downloader.py:21-43` |
| What it does | Strongly-typed result model encapsulating video ID, title, duration in seconds, resolved video/audio paths, and raw metadata dictionary. |
| Industrial-grade? | **A** - Clean, immutable data carrier with helpful debug `__repr__`. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: IngestionService Lifecycle & Stale Fragment Purging
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ingestion/downloader.py:45-98` |
| What it does | Initializes download directories and performs automated cleanup of orphaned `.part*`, `.temp*`, `.ytdl`, and `.ass` files exceeding age thresholds to prevent YouTube CDN HTTP 416 range errors and disk exhaustion. |
| Industrial-grade? | **A** - Essential hygiene preventing stale resume offsets from breaking future download attempts. |
| Checklist | **Error handling:** Pass (defensive try/except on unlinking). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (proactively purges stale partials). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (logs purged counts). **Config:** Pass (`settings.STORAGE_DIR`, `settings.TEMP_DIR`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pre-download fragment purging (Recommended - solves HTTP 416). |
| Priority / Effort | P3 / S |

---

## Unit 3: extract_audio & probe_duration_sec
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ingestion/downloader.py:112-150` |
| What it does | Extracts 16kHz 16-bit mono PCM WAV audio using FFmpeg supervised by `SubprocessWatchdog` (120s timeout, automatic partial cleanup) and probes container duration via `ffprobe` (15s timeout). |
| Industrial-grade? | **A** - Standardized 16kHz mono audio formatting optimized for Whisper speech-to-text models; guarded by watchdog process supervisor. |
| Checklist | **Error handling:** Pass (raises RuntimeError on non-zero exit; returns 0 on probe failure). **Timeouts:** Pass (120s and 15s watchdog limits). **Retries:** N/A. **Idempotency:** Pass (`-y` flag). **Input validation:** Pass. **Resource cleanup:** Pass (`artifacts_to_cleanup=[audio_path]`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (FFmpeg/ffprobe). **Security:** Pass (arg vectors prevent command injection). **Performance:** Pass (fast PCM demuxing). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Supervised FFmpeg 16kHz PCM extraction (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: IngestionService.download_video
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ingestion/downloader.py:152-385` |
| What it does | Downloads YouTube video streams using `yt-dlp` with dynamic client rotation (`android`, `ios`, `mweb`), automated Netscape cookie detection, live telemetry hook, Windows WinError 32 file-locking recovery, HTTP 416 purge recovery, thumbnail extraction, and audio demuxing. |
| Industrial-grade? | **A** - State-of-the-art download resilience engineering incorporating client rotation, cookie discovery, jittered exponential backoff, and Windows file lock mitigation. |
| Checklist | **Error handling:** Pass (custom recovery branches for HTTP 416, WinError 32, and bot detection). **Timeouts:** Pass. **Retries:** Pass (3 attempts with jittered exponential backoff). **Idempotency:** Pass (resolves cached files before downloading). **Input validation:** Pass (normalizes URLs and video IDs). **Resource cleanup:** Pass (cleans temp fragment directory on completion). **Concurrency safety:** Pass (isolated per-video fragment directory). **Atomicity:** Pass (moves completed file into destination). **External-dependency:** Pass (`yt_dlp`). **Security:** Pass (sanitizes filenames via `windowsfilenames=True`). **Performance:** Pass (caps video height at 1080p). **Observability:** Pass (streams progress to `job_progress_tracker`). **Config:** Pass (`settings.STORAGE_DIR`, `settings.YOUTUBE_COOKIE_PATH`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Fails over to mobile clients and cookies automatically if IP encounters anti-bot challenges. |
| Improvement | Keep as is. |
| Better approach? | Option 1: yt-dlp with client rotation and cookie discovery (Recommended - industry standard). Option 2: Pure pytube (Deprecated, brittle, breaks on YouTube cipher changes). |
| Priority / Effort | P3 / S |

---

## Unit 5: IngestionService.get_metadata
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ingestion/downloader.py:387-391` |
| What it does | Fetches YouTube video metadata without initiating stream downloads (`download=False`). |
| Industrial-grade? | **B** - Fast metadata extraction, but does not reuse discovered cookies or player client rotation. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`yt_dlp`). **Security:** Pass. **Performance:** Pass (skips video stream download). **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If YouTube presents a bot challenge to the IP, `get_metadata` will raise an exception because it does not attach `cookiefile` or `extractor_args`. |
| Improvement | Attach discovered cookies and client extractor arguments to `get_metadata` options. |
| Better approach? | Option 1: Share configured cookiefile and player client in `get_metadata` (Recommended). Option 2: Plain unauthenticated options (Current). |
| Priority / Effort | P2 / S |
