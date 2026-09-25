# 10 Architectural Discrepancies & Ground-Truth Reconciliations

**Document Version:** 1.0.0  
**Status:** Verified Complete  
**Last Updated:** 2026-09-24  
**Primary Sources:** [`YouTube Shorts Automation - Project Handover Spec.md`](file:///d:/Projects/YT%20AUTOMATION/YouTube%20Shorts%20Automation%20-%20Project%20Handover%20Spec.md), [`shorts_engine/db/models.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/db/models.py), [`shorts_engine/config/settings.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/config/settings.py), [`shorts_engine/dashboard/auth.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/auth.py)

---

## 1. Discrepancy Ledger: Specification vs. As-Implemented Code

Per **Ground Rule 1**, the codebase is the sole source of truth. All conflicts between the original Handover Spec (v1.0), legacy planning documents, source comments, and the current operational codebase are cataloged below.

| Discrepancy Item | Category | Handover Spec / Legacy Doc Claim | As-Implemented Reality (Code Truth) | Code Citation | Technical Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Data Model Entity Count** | Database | Section 10 specifies **7 entities**: `channels`, `source_videos`, `transcripts`, `clip_candidates`, `clips`, `publish_log`, `sfx_library`. | Implements **9 entities**: Added `platform_publish_logs` and `users`. Models docstring still claims 7 entities. | [`models.py:2`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/db/models.py#L2), [`models.py:259-299`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/db/models.py#L259-L299) | Added to support omnichannel syndication auditing (TikTok/Instagram) and multi-user PBKDF2 RBAC. |
| **Authentication Scheme** | Security | Section 14 and `settings.py` docstrings describe HTTP Basic Authentication (`DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`). | Basic Auth is **completely eradicated**. Implements PBKDF2-HMAC-SHA256 (600,000 rounds), HMAC-SHA256 signed session cookies, CSRF protection, and 3-tier RBAC (`admin`, `operator`, `viewer`). | [`auth.py:21-277`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/auth.py#L21-L277), [`settings.py:331-332`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/config/settings.py#L331-L332) | Industrial security standards require session revocation, password stretching, CSRF defense, and granular role separation. |
| **Face Tracking Engine** | Computer Vision | Section 9.3 and `settings.py:140` describe "MediaPipe face tracking". | Exclusively uses **OpenCV YuNet ONNX** (`face_detection_yunet_2023mar.onnx`) with cosine angle hysteresis. | [`detector.py:24-40`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/reframing/detector.py#L24-L40), [`settings.py:136-141`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/config/settings.py#L136-L141) | MediaPipe suffers severe binary dependency and thread-pool locking issues on Windows Python 3.11/3.12 headless environments. |
| **Syndication Scope** | Publishing | Section 9.9 only describes YouTube Data API v3 publishing. | Implements **Omnichannel Syndication**: YouTube Shorts, TikTok Content Posting API v2, and Instagram Graph API v19.0 Reels via strategy pattern. | [`base.py:22-68`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/base.py#L22-L68), [`tiktok.py:15-180`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/tiktok.py#L15-L180), [`instagram.py:15-190`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/instagram.py#L15-L190) | High-volume clipping workflows require multi-platform audience syndication without separate manual uploads. |
| **A/B Testing & Optimization** | Machine Learning | Not specified in original handover spec (v1.0). | Implements **Bayesian Multi-Armed Bandit** optimizer with conjugate $\text{Beta}(1, 19)$ prior, Monte Carlo sampling, and emergency view-velocity circuit breaker. | [`bandit.py:58-97`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/ab_testing/bandit.py#L58-L97), [`winner_daemon.py:34-140`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/ab_testing/winner_daemon.py#L34-L140) | Closes the loop between post-publish analytics and automated title optimization to maximize algorithmic CTR. |
| **Audio Mastering Pipeline** | DSP / Audio | Section 9.6 mentions simple FFmpeg `loudnorm` filter. | Implements **3-stage broadcast chain**: 80Hz highpass filter, `agate` noise gate, active sidechain compressor ducking (`-14dB`), and dual-pass EBU R128 loudness normalization. | [`mastering.py:28-180`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/audio/mastering.py#L28-L180) | Professional shorts require vocal clarity, music ducking behind dialogue, and standard -14 LUFS / -1 dBFS true-peak loudness. |
| **Subprocess Execution Safety** | Infrastructure | Section 12 implies standard subprocess execution with generic timeouts. | Implements **`SubprocessWatchdog`** managing complete OS process trees with hard kills via `taskkill /F /T` (Windows) and `os.killpg` (POSIX). | [`watchdog.py:21-85`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/watchdog.py#L21-L85) | Standard `Popen.terminate()` orphans FFmpeg child worker threads on Windows, causing file handle lockups and GPU VRAM exhaustion. |
| **Dashboard Default Port** | Network Config | `settings.py:329` defaults `DASHBOARD_PORT = 8080`. | Command-line tool default in `cli.py:346`, batch scripts (`START_DASHBOARD.bat`), and Docker configurations use **`8000`**. | [`cli.py:346`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L346), [`settings.py:329`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/config/settings.py#L329) | Port 8080 frequently collides with local dev proxies; port 8000 is standardized across orchestration. |
| **Default SQLite Database Name** | Database | `settings.py:36` defaults to `storage/shorts_automation.db`. | Operational scripts, batch files, and active storage artifacts use **`storage/shorts_engine.db`**. | [`settings.py:36`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/config/settings.py#L36), [`path_manager.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/storage/path_manager.py) | Harmonized during system rebranding to "Shorts Engine". |
| **Quality Gates Architecture** | QA | Section 9.8 describes a single monolithic LLM verification pass. | Implements **4 decoupled verification tiers**: Fatal Technical Checks (FFprobe), Black/Mute Detection, Computer Vision Framing Inspect, and Semantic LLM check with 1-click Auto-Fixes. | [`technical_check.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/quality/technical_check.py), [`render_inspector.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/quality/render_inspector.py), [`recovery.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/quality/recovery.py) | Fast local deterministic checks must disqualify corrupt renders instantly before expending paid LLM quota. |

---

## 2. Source Code Comments & Docstring Inconsistencies

1. **`shorts_engine/db/models.py:2`**:  
   - *Comment:* `"SQLAlchemy ORM models implementing all 7 entities from Section 10 of SRS."`  
   - *Reality:* File actually defines 9 entities (adding `PlatformPublishLog` and `User`).
2. **`shorts_engine/config/settings.py:140`**:  
   - *Comment:* `"Minimum confidence threshold for MediaPipe face tracking"`  
   - *Reality:* Setting controls the threshold passed to OpenCV YuNet ONNX face detector (`shorts_engine/services/reframing/detector.py:35`).
3. **`shorts_engine/config/settings.py:331-332`**:  
   - *Comment:* `"HTTP Basic Auth username"`, `"HTTP Basic Auth password"`  
   - *Reality:* Legacy fields remain in Pydantic schema for environment backward compatibility, but `dashboard/app.py` enforces cookie and bearer token auth with PBKDF2 hashing in SQLite/Postgres.

---

## 3. Technical Debt & Architectural Gaps

1. **Dual Default Port Definitions:**  
   - While `settings.py` sets `DASHBOARD_PORT = 8080`, running `python run.py dashboard` defaults to `8000` unless overridden. While harmless due to explicit batch script flags, it introduces confusion if invoked without arguments.
2. **Settings Singleton Mutation:**  
   - `save_runtime_settings()` writes to `storage/config/runtime_settings.json` and updates `settings` in-memory. However, in multi-process deployments without IPC (e.g., separate worker process and web process), the worker re-reads the JSON on every poll loop, while the web process mutates in place. This is handled gracefully via file polling, but lacks a centralized Redis/PubSub configuration bus.
3. **Lazy Import Pattern in CLI:**  
   - Several CLI commands in `cli.py` use function-scoped lazy imports (`from shorts_engine.services.storage.backup_manager import DatabaseBackupManager`) to keep CLI startup snappy (<200ms) without loading PyTorch or OpenCV until required. While intentional, it bypasses static type verification at module level.
