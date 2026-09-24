# Finding-to-Plan Reconciliation Matrix (v5)

This document reconciles all 242 audit cards and every finding identified during the exhaustive line-by-line codebase audit against the industrial-grade remediation plan (`docs/audit/05_plan.md` v5).

---

## 1. Audit Grade Summary

- **Total Assessed Files:** 242
- **Total Functional Units Audited:** 387
- **Unit Grade Breakdown (Post-Adversarial Regrading):**
  - **Grade A:** 338 units (87.3%)
  - **Grade B / B+:** 39 units (10.1%)
  - **Grade C / C+:** 1 unit (0.3%)
  - **Grade D:** 9 units (2.3%) — Regraded from B/C due to fatal crashes, data loss, or silent database/session corruption.
- **Skeptic-Pass Miss Rate:** 3 / 15 sampled units = **20.0%** (or 9 / 49 non-A units = **18.4%** across all modules).
- **Total Non-A Units Requiring Reconciliation:** 49 units across 24 files + 1 architectural circular dependency.
- **Orphan Count:** **0** (Every non-A unit is mapped to a Plan Item, an Accepted Risk, or a Rejected Proposal).

---

## 2. Reconciliation Matrix

| Finding ID | Source Module & Unit / Location | Audit Grade | Finding Description | Disposition | Target Plan Item / Rationale |
| :--- | :--- | :---: | :--- | :---: | :--- |
| **F-01** | `find channel id.txt:1-4` | **C** | Ad-hoc text file in root containing manual browser steps to find channel IDs. | **Deferred** | `PLAN-P3-01`: Clean Git Index & Consolidate Documentation. |
| **F-02** | `folder_structure.txt:1-1031` | **B** | Static directory tree text dump tracked in git index, causing git bloat. | **Deferred** | `PLAN-P3-01`: Clean Git Index & Consolidate Documentation. |
| **F-03** | `pyproject.toml:5-38` | **B** | `groq` and `scipy` declared in `requirements.txt` are missing from `pyproject.toml` dependencies. | **Tier 1** | `PLAN-P1-04`: Align pyproject.toml Dependencies. |
| **F-04** | `QA_VERIFICATION_PROTOCOL_previous version.txt:1-279` | **B+** | Historical verification protocol kept in project root. | **Accepted Risk** | Historical reference file; does not impact runtime execution. |
| **F-05** | `.github/workflows/ci-cd.yml:1-117` | **B** | Deployment stage contains placeholder echo commands. | **Accepted Risk** | Deferred until cloud production CD target environment is provisioned. |
| **F-06** | `.github/workflows/ci.yml:1-109` | **B+** | Missing `timeout-minutes` on CI jobs; `pip-audit ... \|\| true` swallows vulnerability alerts. | **Tier 2** | `PLAN-P1-05`: Harden GitHub Actions CI Workflow (`pip-audit` separate commit). |
| **F-07** | `shorts_engine/config/logging_config.py:88-129` | **B** | `reload_runtime_settings` reads file without concurrency locking or retry on partial write. | **Tier 1** | `PLAN-P0-02`: Atomic File Writes for Runtime State. |
| **F-08** | `shorts_engine/config/logging_config.py:131-153` | **D** | `save_runtime_settings` uses direct `json.dump`, risking 0-byte file truncation on power failure. | **Tier 1** | `PLAN-P0-02`: Atomic File Writes for Runtime State. |
| **F-09** | `shorts_engine/config/settings.py:41-48` | **B** | Default development master encryption key used if environment variable not set; no guard refuses placeholders. | **Tier 1** | `PLAN-P1-08`: Enforce Production Secret Placeholder Guard in Settings (Code only; deferred enforcement). |
| **F-10** | `shorts_engine/db/security.py:88-114` | **B** | `rotate_all_channel_tokens` / `cli.py:46` takes secrets via CLI args and lacks pre-rotation DB backup. | **Tier 2** | `PLAN-P1-10`: Harden Secret Rotation with Automated Backup and Secure Input (`rotate-secrets`). |
| **F-11** | `shorts_engine/db/session.py:67-104` | **D** | `ensure_schema_upgrades` swallows `ALTER TABLE` exceptions without alert telemetry or fail-fast check. | **Tier 2** | `PLAN-P1-09`: Fail-Fast Schema Migrations & Explicit Index Management (Includes `source_videos` unique index). |
| **F-12** | `shorts_engine/orchestration/lifecycle.py:13-57` | **B+** | In-memory status check (`video.status != expected_status`) before `db.commit()` allows worker race. | **Tier 1** | `PLAN-P1-06`: Atomic Lifecycle State Transitions. |
| **F-13** | `shorts_engine/orchestration/runner.py:679` | **D** | **Critical Crash:** Resumed pipeline fails with `AttributeError` when `formatted_transcript.words` contains dicts. | **Tier 1** | `PLAN-P0-03`: Resumed Pipeline Transcript Normalization (Strict load-time normalization). |
| **F-14** | `shorts_engine/services/ab_testing/analytics_ingestion.py:49-142` | **B** | Hardcoded static calendar year strings `startDate="2026-01-01"` and `endDate="2026-12-31"`. | **Tier 1** | `PLAN-P1-11`: Dynamic Analytics Date Window Calculation (Must ship before 2027-01-01). |
| **F-15** | `shorts_engine/services/audio/mastering.py:104-136` | **B** | Missing timeout parameter on `subprocess.run(["ffmpeg", ...])` in `measure_loudness`. | **Tier 1** | `PLAN-P1-02`: Enforce Subprocess Timeouts on Media Probes. |
| **F-16** | `shorts_engine/services/detection/detector.py:36-78` | **B** | Race condition between WebSub push and RSS poll creates duplicate `SourceVideo` records. | **Tier 2** | `PLAN-P0-04`: Ingestion Deduplication Mutex & Constraint (Runs after P1-09; stop and report if duplicates exist). |
| **F-17** | `shorts_engine/services/detection/filter.py:13-41` | **B** | Duration-only check flags short 16:9 landscape videos as Shorts. | **Accepted Risk** | Intended behavior for initial MVP; full aspect-ratio detection planned for post-MVP. |
| **F-18** | `shorts_engine/services/detection/rss_poller.py:96-143` | **B** | Empty `new_video_ids` when creator uploads only Shorts causes stuck poller cursor. | **Deferred** | `PLAN-P2-04`: Advance RSS Poller Cursor on Skipped Shorts. |
| **F-19** | `shorts_engine/services/detection/rss_poller.py:145-176` | **B** | `time.sleep(interval)` blocks OS signal termination (SIGTERM/SIGINT) during poll loop. | **Deferred** | `PLAN-P2-05`: Interruptible Poller Event Sleep. |
| **F-20** | `shorts_engine/services/detection/websub.py:37-48` | **D** | `websub_subscriptions.json` written via direct `write_text`, risking truncation on abort. | **Tier 1** | `PLAN-P0-02`: Atomic File Writes for Runtime State. |
| **F-21** | `shorts_engine/services/ingestion/downloader.py:387-391` | **B** | `get_metadata` does not attach `cookiefile` or extractor args, failing on bot challenges. | **Deferred** | `PLAN-P2-06`: Attach Bot-Bypass Options to Downloader Metadata Probe. |
| **F-22** | `shorts_engine/services/llm/quota_tracker.py:77-105` | **D** | `api_key_usage.json` saved via direct `write_text`, risking state corruption. | **Tier 1** | `PLAN-P0-02`: Atomic File Writes for Runtime State. |
| **F-23** | `shorts_engine/services/llm/quota_tracker.py:369, 523` | **B** | Duplicate `remove_key` method in `QuotaTracker` shadows the first implementation. | **Deferred** | `PLAN-P3-02`: Remove Shadowed Dead Code in Quota Tracker. |
| **F-24** | `shorts_engine/services/monitoring/alerter.py:1-131` | **B+** | Synchronous blocking `urllib.request` webhook calls stall worker threads on network timeouts. | **Deferred** | `PLAN-P2-07`: Non-Blocking Async/Threadpool Webhook Dispatch. |
| **F-25** | `shorts_engine/services/monitoring/analytics_sync.py:1-89` | **D** | **Data Corruption:** `_fetch_clip_metrics` falls back to `source_video.youtube_video_id`, corrupting clip stats. | **Tier 1** | `PLAN-P0-01`: Fix Analytics Video ID Misbinding (Code fix only; data repair report-only). |
| **F-26** | `shorts_engine/services/publishing/client.py:22-88` | **B+** | `next_chunk()` resumable upload loop lacks exponential backoff on transient network drops. | **Tier 2** | `PLAN-P1-03`: Resumable Upload Chunk Retry with Exponential Backoff. |
| **F-27** | `shorts_engine/services/publishing/drip_feed.py:45-101` | **B** | Concurrent workers running `check_and_publish_due_clips` can double-publish due to lack of lock. | **Tier 2** | `PLAN-P0-05`: Drip-Feed Publishing Row Lock, Upload Started Marker, Channel Upload Check & Stale Reclaim (Re-rated P1). |
| **F-28** | `shorts_engine/services/publishing/instagram.py:128` | **B+** | `_upload_direct_binary` loads full MP4 into memory buffer via `f.read()`. | **Tier 2** | `PLAN-P2-11`: Enforce 50MB File Size Guard and Chunked Streaming in Instagram Publisher. |
| **F-29** | `shorts_engine/services/publishing/manager.py:120-219` | **D** | Multi-threaded platform syndicators share a single non-thread-safe SQLAlchemy `Session`. | **Tier 2** | `PLAN-P1-07`: Scoped Database Sessions in Multi-Platform Syndicator (Prove shared session with test first). |
| **F-30** | `shorts_engine/services/publishing/quota.py:23-54` | **D** | `quota_state.json` saved via direct `write_text`, risking truncation on abort; lacks concurrency lock. | **Tier 1** | `PLAN-P0-02` (Atomic File Writes) & `PLAN-P1-12` (Concurrency Lock on Quota State). |
| **F-31** | `shorts_engine/services/quality_gate/recovery.py:221-370` | **D** | **Crash on Auto-Fix:** Argument mismatches in `caption_safe_rerender` & `FileExistsError` on Windows `rename()`. | **Tier 1** | `PLAN-P0-06`: Clip Recovery Auto-Fixes & Decoupling from Rendering (Merged with P1-01; dependency injection). |
| **F-32** | `shorts_engine/services/quality_gate/render_inspector.py:22-103` | **B+** | OpenCV `cap.release()` not enclosed in `try...finally` block, risking open file leak on exception. | **Deferred** | `PLAN-P2-08`: OpenCV VideoCapture Resource Cleanup Guardrails. |
| **F-33** | `shorts_engine/services/quality_gate/technical_check.py:16-91` | **B+** | Missing timeout parameter on `subprocess.run(["ffprobe", ...])`, risking worker hang on damaged atoms. | **Tier 1** | `PLAN-P1-02`: Enforce Subprocess Timeouts on Media Probes. |
| **F-34** | `shorts_engine/services/reframing/cropper.py:52-182` | **B+** | OpenCV `cap.release()` not enclosed in `try...finally` block, risking open file leak on exception. | **Deferred** | `PLAN-P2-08`: OpenCV VideoCapture Resource Cleanup Guardrails. |
| **F-35** | `shorts_engine/services/rendering/compositor.py:34-47` | **B** | `get_default_fontfile` returns `None` on containerized Linux lacking DejaVu fonts. | **Deferred** | `PLAN-P2-09`: Font File Fallback & Container Font Packaging. |
| **F-36** | `shorts_engine/services/rendering/compositor.py:428-501` | **B+** | Missing timeout parameter on `subprocess.run(["ffprobe", ...])` in `validate_render`. | **Tier 1** | `PLAN-P1-02`: Enforce Subprocess Timeouts on Media Probes. |
| **F-37** | `shorts_engine/services/rendering/thumbnail_generator.py:36-58` | **B** | Missing timeout parameter on `subprocess.run(["ffmpeg", ...])` in `extract_frame_ffmpeg`. | **Tier 1** | `PLAN-P1-02`: Enforce Subprocess Timeouts on Media Probes. |
| **F-38** | `shorts_engine/services/rendering/trimmer.py:30-80` | **B+** | Signature parameter `min_silence_gap_sec` is never evaluated or enforced in boundary snapping. | **Deferred** | `PLAN-P3-03`: Enforce or Deprecate min_silence_gap_sec in Trimmer. |
| **F-39** | `shorts_engine/services/rendering/trimmer.py:91-260` | **B+** | Re-trimming an existing clip leaves previous rendered MP4 file orphaned on disk. | **Tier 1** | `PLAN-P0-06`: Clip Recovery Auto-Fixes, Protocol Decoupling & Old Render Cleanup (Unlink old rendered file). |
| **F-40** | `shorts_engine/services/transcription/aligner.py:14-53` | **B** | `ForcedAligner` uses heuristic interval smoothing rather than acoustic CTC forced alignment. | **Accepted Risk (Assumed)** | Rationale assumed: +/-50ms precision was not empirically measured against audio; acoustic measurement test added to plan. |
| **F-41** | `Shorts_Engine_Control_Center/START_HERE.bat` | **B+** | Machine-specific hardcoded absolute paths (`D:\Projects\...`). | **Deferred** | `PLAN-P2-01`: Portable Windows Control Center Path Resolution. |
| **F-42** | `Shorts_Engine_Control_Center/.../start_dashboard.bat` | **B+** | Machine-specific hardcoded absolute paths (`D:\Projects\...`). | **Deferred** | `PLAN-P2-01`: Portable Windows Control Center Path Resolution. |
| **F-43** | `Shorts_Engine_Control_Center/.../start_worker.bat` | **B+** | Machine-specific hardcoded absolute paths (`D:\Projects\...`). | **Deferred** | `PLAN-P2-01`: Portable Windows Control Center Path Resolution. |
| **F-44** | `Shorts_Engine_Control_Center/.../run_all_tests.bat` | **B+** | Machine-specific hardcoded absolute paths (`D:\Projects\...`). | **Deferred** | `PLAN-P2-01`: Portable Windows Control Center Path Resolution. |
| **F-45** | `Shorts_Engine_Control_Center/.../run_dashboard_tests.bat` | **B+** | Machine-specific hardcoded absolute paths (`D:\Projects\...`). | **Deferred** | `PLAN-P2-01`: Portable Windows Control Center Path Resolution. |
| **F-46** | `Shorts_Engine_Control_Center/.../add_channel.bat` | **B+** | Machine-specific hardcoded absolute paths (`D:\Projects\...`). | **Deferred** | `PLAN-P2-01`: Portable Windows Control Center Path Resolution. |
| **F-47** | `Shorts_Engine_Control_Center/.../process_video.bat` | **B+** | Machine-specific hardcoded absolute paths (`D:\Projects\...`). | **Deferred** | `PLAN-P2-01`: Portable Windows Control Center Path Resolution. |
| **F-48** | `Shorts_Engine_Control_Center/.../build_bundle.bat` | **B+** | Hardcoded paths & missing `.env` zip exclusion risks packing active secrets. | **Approved with Change** | `PLAN-P0-07`: Secure Distribution Bundle Packaging (`fnmatch` pattern matching). |
| **F-49** | `Shorts_Engine_Control_Center/.../create_user.bat` | **B+** | Hardcoded paths & console prompt echoes operator password in plaintext. | **Approved with Change** | `PLAN-P2-12`: Mask Password Input in User Creation (`getpass` prompt). |
| **F-50** | `recovery.py` $\leftrightarrow$ `compositor.py` | **N/A** | Architectural Circular Import Cycle detected during static dependency analysis. | **Tier 1** | `PLAN-P0-06`: Decouple Circular Import Between Quality Gate & Rendering (Dependency injection). |
| **F-51** | `shorts_engine/services/quality/media_qa.py:40-100` | **B+** | Two independent FFmpeg passes decode media twice for black frames and audio silence. | **Deferred** | `PLAN-P2-02`: Single-Pass Combined FFmpeg Media QA Filtergraph. |
