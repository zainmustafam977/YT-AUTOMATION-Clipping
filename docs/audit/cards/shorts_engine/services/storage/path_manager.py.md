# Assessment Cards: shorts_engine/services/storage/path_manager.py

---

## Unit 1: slugify
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:20-37` |
| What it does | Sanitizes arbitrary text into deterministic, filesystem-safe ASCII slugs by normalizing NFKD unicode, removing illegal characters, and enforcing length caps to respect Windows MAX_PATH (260 characters). |
| Industrial-grade? | **A** - Resilient handling of unicode, empty strings, and Windows filesystem forbidden characters (`:`, `*`, `?`, `"`, `<`, `>`, `|`). |
| Checklist | **Error handling:** Pass (empty/whitespace input returns "unnamed"). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure function). **Atomicity:** Pass. **External-dependency:** Pass (standard library). **Security:** Pass (prevents path traversal characters). **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Regex + NFKD slugification with MAX_PATH budgeting (Recommended - zero external deps). Option 2: `python-slugify` library (Adds third-party dependency for identical outcome). |
| Priority / Effort | P3 / S |

---

## Unit 2: Channel & Video Folder Name Generators
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:45-56` |
| What it does | Formats human-readable yet deterministic folder names combining a slugged title/name and unique ID suffix. |
| Industrial-grade? | **A** - Balances human navigation in Windows Explorer with machine uniqueness. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: Download Path Resolvers (get_download_dir, get_download_video_path, get_download_audio_path)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:57-98` |
| What it does | Constructs deterministic paths for source MP4 video streams and extracted 16kHz mono WAV audio tracks under a channel-scoped hierarchy, auto-creating parent directories. |
| Industrial-grade? | **A** - `mkdir(parents=True, exist_ok=True)` eliminates race conditions during folder initialization. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (`exist_ok=True`). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (paths constructed via `Path` algebra). **Performance:** Pass. **Observability:** N/A. **Config:** Pass (`settings.STORAGE_DIR`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: Output & Temp Path Resolvers (get_output_dir, get_clip_output_path, get_thumbnail_output_path, get_temp_dir, get_subtitle_path)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:100-161` |
| What it does | Constructs standardized paths for rendered clips, JPEG thumbnails, ephemeral ASS subtitle files, and scratch directories. |
| Industrial-grade? | **A** - Consistent naming convention incorporating start/end millisecond timestamps ensures zero collision between clips of the same video. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: resolve_clip_path
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:163-214` |
| What it does | Two-tier fallback resolver for clip files: checks direct path, checks `clip.rendered_path`, strips `CLAIMED:` worker leases, and performs glob search across hierarchical and legacy flat output locations. |
| Industrial-grade? | **A** - Guarantees zero downtime during migrations from flat to hierarchical storage layouts. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (handles string, Path, or Clip model). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (fast path is O(1); glob only on missing file). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Globbing on huge directories can be slow if hundreds of thousands of files exist. Fast-path `exists()` avoids this in 99.9% of calls. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Fast-path existence check with glob fallback (Recommended). Option 2: Strict DB path only (Breaks if files moved or storage path reconfigured). |
| Priority / Effort | P3 / S |

---

## Unit 6: resolve_download_path
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:216-244` |
| What it does | Two-tier fallback resolver for source video MP4 files, checking direct path, database property, legacy flat storage, and hierarchical search. |
| Industrial-grade? | **A** - Seamless backward compatibility. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 7: safe_unlink
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:246-264` |
| What it does | Safely deletes a file with retry backoff to survive Windows WinError 32 (file in use by another process or lingering file handle). |
| Industrial-grade? | **A** - Vital for Windows OS stability where antivirus, search indexers, or recently closed subprocesses hold transient file locks. |
| Checklist | **Error handling:** Pass (logs warning on final attempt, returns boolean). **Timeouts:** Pass (total backoff delay 3.0s). **Retries:** Pass (3 attempts with exponential delay). **Idempotency:** Pass (`missing_ok=True`). **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (warning on failure). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | On stubborn locks, 3 retries (0.5s, 1.0s, 1.5s) might delay the worker thread up to 3 seconds before reporting failure. |
| Improvement | Call `gc.collect()` before retrying on Windows to release lingering Python file handles. |
| Better approach? | Option 1: Backoff retry with `gc.collect()` (Recommended for Windows). Option 2: Immediate unhandled `p.unlink()` (Crashes on Windows with WinError 32). |
| Priority / Effort | P2 / S |

---

## Unit 8: prune_empty_dirs
| Field | Content |
|---|---|
| Location | `shorts_engine/services/storage/path_manager.py:266-282` |
| What it does | Recursively prunes empty parent directories upward until reaching the specified `stop_at` boundary. |
| Industrial-grade? | **A** - Uses `is_relative_to` to enforce a strict upper boundary, preventing accidental deletion of root storage directories. |
| Checklist | **Error handling:** Pass (catches exceptions and logs debug). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (cleans empty directories). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (boundary guarded by `is_relative_to`). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Upward traversal with boundary stop (Recommended). Option 2: `shutil.rmtree` (Dangerous; deletes non-empty directories). |
| Priority / Effort | P3 / S |
