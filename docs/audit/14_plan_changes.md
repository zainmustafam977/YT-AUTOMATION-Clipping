# Plan Changes Register: Reconciliation of Plan v1 to v2

This document details every addition, revision, re-prioritization, and structural change made between `docs/audit/05_plan.md` v1 and `docs/audit/05_plan.md` v2.

---

## 1. Summary of Differences

| Metric | Plan v1 | Plan v2 | Difference / Rationale |
| :--- | :---: | :---: | :--- |
| **Total Plan Items** | 5 | 29 | **+24 items added.** v1 only captured top-level findings from the initial summary, leaving 43 findings orphaned. v2 achieves 100% reconciliation with zero orphans. |
| **P0 Items (Critical)** | 0 | 8 | **+8 items elevated/added to P0.** Re-triaged according to the strict definition: data loss, security exposure, crashes, or silently wrong results. |
| **P1 Items (High)** | 1 | 7 | **+6 items added to P1.** Enforces subprocess timeouts, circular dependency decoupling, CI/CD gates, and atomic state transitions. |
| **P2 Items (Medium)** | 3 | 10 | **+7 items added to P2.** Performance optimizations, single-pass FFmpeg Media QA, resource cleanup guardrails, and operational script portability. |
| **P3 Items (Low)** | 1 | 4 | **+3 items added to P3.** Dead code removal, git index cleanup, and boundary snapping cleanup. |
| **Data Repair Protocol** | None | Included | Added explicit detection SQL and repair script for analytics video ID misbinding. |
| **Pre-Mortem Analysis** | None | Included | Added failure mechanism and prevention guardrails for every fix. |
| **Decisions Catalog** | None | Included | Integrated recommendations from `04_alternatives.md` with trade-offs and Mermaid diagram. |
| **Secret & History Check** | None | Included | Added full git commit history scan findings and manual operator rotation checklist. |

---

## 2. Item-by-Item Breakdown of Changes

### 2.1 Items Promoted to Priority 0 (Critical)

1. **`PLAN-P0-01`: Fix Analytics Video ID Misbinding & Data Repair (Was P2 in v1)**
   - *Rationale:* Under the strict definition (*silently wrong results / data corruption*), writing long-form video metrics into clip records directly corrupts historical data and fools the Bayesian bandit A/B promotion daemon into picking false winners. Elevated from P2 to P0.
   - *Addition:* Added past record impact assessment, detection SQL query, and `cli.py repair-analytics` repair protocol.

2. **`PLAN-P0-02`: Atomic File Writes for Runtime State & Quotas (New in v2)**
   - *Rationale:* Resolves findings `F-07`, `F-08`, `F-20`, `F-22`, `F-30`. Direct `write_text` and `json.dump` calls across `logging_config.py`, `websub.py`, `quota_tracker.py`, and `publishing/quota.py` cause 0-byte file truncation on sudden process aborts (*data loss*).

3. **`PLAN-P0-03`: Resumed Pipeline Transcript Normalization (New in v2)**
   - *Rationale:* Resolves finding `F-13`. Resuming an existing pipeline crashes with `AttributeError: 'dict' object has no attribute 'word'` on line 679 of `runner.py` (*application crash*).

4. **`PLAN-P0-04`: Ingestion Deduplication Mutex & Constraint (New in v2)**
   - *Rationale:* Resolves finding `F-16`. Concurrency race between WebSub callbacks and RSS polling creates duplicate `source_videos` rows, duplicating transcription and rendering compute costs (*silently wrong state*).

5. **`PLAN-P0-05`: Drip-Feed Publishing Row Lock / Mutex (New in v2)**
   - *Rationale:* Resolves finding `F-27`. Concurrent workers executing `check_and_publish_due_clips` pick up the same due clips and upload duplicate Shorts to YouTube (*silently wrong results / duplicate publishing*).

6. **`PLAN-P0-06`: Fix Clip Recovery Auto-Fix Interfaces & Atomic Replace (New in v2)**
   - *Rationale:* Resolves finding `F-31`. In `recovery.py`, `caption_safe_rerender` crashes on invalid argument names (`TypeError`), and `technical_clamp` crashes on Windows with `FileExistsError` during `out_path.rename(in_path)` (*application crash*).

7. **`PLAN-P0-07`: Secure Distribution Bundle Packaging (New in v2)**
   - *Rationale:* Resolves finding `F-48`. `build_bundle.bat` packages `.env` and `*.key` files into zip archives without exclusion (*security exposure*).

8. **`PLAN-P0-08`: Mask Password Input in User Creation Script (New in v2)**
   - *Rationale:* Resolves finding `F-49`. `create_user.bat` echoes operator passwords in plaintext on the terminal console (*security exposure*).

---

### 2.2 Items Added to Priority 1 (High)

1. **`PLAN-P1-02`: Enforce Subprocess Timeouts on All Media Probes (New in v2)**
   - *Rationale:* Resolves findings `F-15`, `F-33`, `F-36`, `F-37`. Unbounded `subprocess.run` calls in `technical_check.py`, `compositor.py:validate_render`, `thumbnail_generator.py`, and `audio/mastering.py` can freeze workers indefinitely on damaged media atoms (*production stability / deadlock*).

2. **`PLAN-P1-03`: Resumable Upload Chunk Retry with Exponential Backoff (New in v2)**
   - *Rationale:* Resolves finding `F-26`. Transient network hiccups abort multi-megabyte uploads in `client.py`.

3. **`PLAN-P1-04`: Align pyproject.toml Dependencies (New in v2)**
   - *Rationale:* Resolves finding `F-03`. Missing `groq` and `scipy` in `pyproject.toml` breaks standard packaging installations.

4. **`PLAN-P1-05`: Harden GitHub Actions CI Workflow (New in v2)**
   - *Rationale:* Resolves finding `F-06`. Unbounded job runtimes and swallowed `pip-audit` alerts weaken CI verification gates.

5. **`PLAN-P1-06`: Atomic Lifecycle State Transitions (New in v2)**
   - *Rationale:* Resolves finding `F-12`. Eliminates in-memory race condition during video status transitions.

6. **`PLAN-P1-07`: Scoped Database Sessions in Multi-Platform Syndicator (New in v2)**
   - *Rationale:* Resolves finding `F-29`. Prevents multi-threaded syndicators from sharing non-thread-safe SQLAlchemy sessions.

---

### 2.3 Items Added to Priority 2 (Medium)

1. **`PLAN-P2-03`: Dynamic Analytics Date Window Calculation (New in v2)**
   - *Rationale:* Resolves finding `F-14`. Replaces hardcoded 2026 strings with dynamic date math.

2. **`PLAN-P2-04`: Advance RSS Poller Cursor on Skipped Shorts (New in v2)**
   - *Rationale:* Resolves finding `F-18`. Prevents stuck cursor when channels upload only Shorts.

3. **`PLAN-P2-05`: Interruptible Poller Event Sleep (New in v2)**
   - *Rationale:* Resolves finding `F-19`. Enables immediate SIGTERM/SIGINT shutdown during sleep.

4. **`PLAN-P2-06`: Attach Bot-Bypass Options to Downloader Metadata Probe (New in v2)**
   - *Rationale:* Resolves finding `F-21`. Attaches cookiefile and extractor options to `get_metadata`.

5. **`PLAN-P2-07`: Non-Blocking Async/Threadpool Webhook Dispatch (New in v2)**
   - *Rationale:* Resolves finding `F-24`. Offloads synchronous webhook calls from worker pipeline threads.

6. **`PLAN-P2-08`: OpenCV VideoCapture Resource Cleanup Guardrails (New in v2)**
   - *Rationale:* Resolves findings `F-32`, `F-34`. Wraps `cap.release()` in `try...finally` blocks in `render_inspector.py` and `cropper.py`.

7. **`PLAN-P2-09`: Font File Fallback & Container Font Packaging (New in v2)**
   - *Rationale:* Resolves finding `F-35`. Prevents subtitle failure on minimal Linux containers.

8. **`PLAN-P2-10`: Cleanup Orphaned Render Artifacts on Re-trim (New in v2)**
   - *Rationale:* Resolves finding `F-39`. Unlinks old MP4 files when re-trimming to prevent disk leakage.

---

### 2.4 Items Added to Priority 3 (Low)

1. **`PLAN-P3-02`: Remove Shadowed Dead Code in Quota Tracker (New in v2)**
   - *Rationale:* Resolves finding `F-23`. Deletes duplicate `remove_key` method definition.

2. **`PLAN-P3-03`: Clean Git Index & Consolidate Documentation (New in v2)**
   - *Rationale:* Resolves findings `F-01`, `F-02`. Untracks `find channel id.txt` and `folder_structure.txt` from git index.

3. **`PLAN-P3-04`: Enforce or Deprecate min_silence_gap_sec in Trimmer (New in v2)**
   - *Rationale:* Resolves finding `F-38`. Cleans up unreferenced parameter in `snap_to_word_boundary`.
