# Phase 5: Industrial-Grade Remediation Plan (v4)

---

## 1. Governance & Strict Approval Gate

In accordance with the Principal Engineer Charter and the operational mode (**"Audit, wait for approval, then implement P0+P1"**), **no application code has been modified**. This plan reconciles 100% of audit findings, establishes exact evidence-backed fixes, pre-mortems, test specifications, and data repair protocols.

**Implementation of this plan will proceed strictly upon explicit user approval.**
Where user directives conflict with previous plan text, the user directives win.

---

## 2. Priority Taxonomy & Tier Execution Structure

- **Tier 1 (Code Only - Stop for Review After the Tier):**
  - `PLAN-P0-01`: Fix Analytics Video ID Misbinding (Warn only if status implies published but ID is missing; otherwise skip quietly; data repair stays report-only).
  - `PLAN-P0-02`: Atomic File Writes & Atomic Replace Helper in `shorts_engine/utils/fs.py` (Leaf module with zero project imports; no `except Exception: pass`, catch `OSError` and log; Windows retry loop).
  - `PLAN-P0-03`: Resumed Pipeline Transcript Normalization (Strict load-time normalization; no silent 0.0 defaults; `WordSegment` citation; supports legacy word lists and dicts).
  - `PLAN-P0-06`: Clip Recovery Auto-Fixes & Decoupling from Rendering (Merged with P1-01; dependency injection only; uses single timeout helper from P1-02; includes P2-10 unlinking old MP4; 0 cycles).
  - `PLAN-P1-02`: Enforce Subprocess Timeouts on All Media Probes (`psutil` declared; unified `run_subprocess_with_timeout` helper covering all 5 unprotected call sites plus `technical_check.py:84`).
  - `PLAN-P1-04`: Align pyproject.toml Dependencies (`groq>=0.9.0`, `scipy>=1.11.0`).
  - `PLAN-P1-06`: Atomic Lifecycle State Transitions (Conditional SQL atomic update).
  - `PLAN-P1-08`: Enforce Production Secret Placeholder Guard in Settings (Code only; feature-flagged off behind `ENFORCE_SECRET_ENTROPY=false` until keys are rotated; launchers do not set `ENV=development` for real runs; real fix is key rotation then `ENV=production`).
  - `PLAN-P1-11`: Dynamic Analytics Date Window Calculation (Window starts at clip's publish date using timezone-aware UTC; must ship before 2027-01-01).
  - `PLAN-P1-12`: Cross-Process File Lock / SQLite-Backed Counter on Quota State (Guards both `publishing/quota.py` and `quota_tracker.py` across multi-process workers).

- **Tier 2 (Database Backup First - Ask User Before Running):**
  - `PLAN-P1-09`: Fail-Fast Schema Migrations & Unique Index Management (Pre-flight duplicate check: if duplicates exist, log ERROR listing them and continue booting without creating index; `P0-04` stays blocked until user decides; adds status and heartbeat columns for P0-05).
  - `PLAN-P0-04`: Ingestion Deduplication Constraint (Runs strictly after P1-09; blocked if duplicates exist).
  - `PLAN-P0-05`: Drip-Feed Publishing Row Lock, Upload Started Marker, PlaylistItems Upload Check & Stale Reclaim (Re-rated P1; queries `playlistItems` of uploads playlist; matches clips by `[clip:<id>]` token in description; tested with unlisted upload).
  - `PLAN-P1-10`: Harden Secret Rotation with Automated Backup and Secure Input (`rotate-secrets` reads keys from hidden prompt or env vars; automatic backup before single transaction).
  - `PLAN-P1-03`: Resumable Upload Chunk Retry with Exponential Backoff (3 retries; fast-fail on `quotaExceeded` / 401 / 403).
  - `PLAN-P1-05`: Harden GitHub Actions CI Workflow (`pip-audit` as a separate non-blocking commit).
  - `PLAN-P1-07`: Scoped Database Sessions in Multi-Platform Syndicator (Prove shared session with test first).
  - `PLAN-P2-11`: Enforce 50MB File Size Guard and Chunked Streaming in Instagram Publisher.

- **Approved with Change:**
  - `PLAN-P0-07`: Secure Distribution Bundle Packaging (Exclude `client_secret*.json`, `credentials*.json`, `token*.json`, `*cookie*.txt`, `.env*`; explicit allow rule for `.env.example`; zip test).
  - `PLAN-P2-12`: Mask Password Input via `getpass` in CLI User Creation (Remove/warn `--password` flag; support env var bootstrap).

- **Deferred Items:**
  - Remaining P2 items (`PLAN-P2-01`, `PLAN-P2-02`, `PLAN-P2-04`, `PLAN-P2-05`, `PLAN-P2-06`, `PLAN-P2-07`, `PLAN-P2-08`, `PLAN-P2-09`).
  - All P3 items (`PLAN-P3-01`, `PLAN-P3-02`, `PLAN-P3-03`).
  - The `F-40` acoustic forced alignment benchmark.

- **Rejected Items:** None.

---

## 3. Prioritized Remediation Catalog

### Priority 0 & Priority 1: Tier 1 Remediation Items (Code Only)

#### PLAN-P0-01: Fix Analytics Video ID Misbinding & Report-Only Data Repair
- **Finding ID:** `F-25`
- **Audit Grade:** Regraded to **D** (Silent data corruption / False A/B promotion).
- **Evidence Call Site:** `shorts_engine/services/monitoring/analytics_sync.py:47-50`
  ```python
  if clip.publish_log and clip.publish_log.youtube_video_id:
      yt_id = clip.publish_log.youtube_video_id
  elif clip.candidate and clip.candidate.source_video:
      yt_id = clip.candidate.source_video.youtube_video_id
  ```
- **Impact:** **Severe Data Corruption & False A/B Promotion.** If a clip lacks a `publish_log`, the daemon queries YouTube metrics for the *source video* (long-form upload) and records those view counts into `clip.analytics_json`.
- **Likelihood:** High (deterministic execution on every unpublished clip evaluated by the daemon).
- **Exact Code Change (Incorporating Amendment):**
  In `shorts_engine/services/monitoring/analytics_sync.py:46-56`:
  ```python
  if not clip.publish_log or not clip.publish_log.youtube_video_id:
      # Warn only if status implies published but the YouTube ID is missing
      if str(getattr(clip, "status", "")).upper() in ("PUBLISHED", "CLIPSTATUS.PUBLISHED"):
          logger.warning("Clip %s has PUBLISHED status but no published YouTube ID; skipping analytics sync.", clip.id)
      # Otherwise skip quietly (candidate, pending, rendered, etc.)
      continue
  yt_id = clip.publish_log.youtube_video_id
  metrics = self._fetch_clip_metrics(clip.id, yt_id)
  if metrics is None:
      continue
  clip.analytics = metrics
  synced_count += 1
  total_views += metrics.get("views", 0)
  total_likes += metrics.get("likes", 0)
  ```
- **Effort:** Small (S)
- **Risk of Change:** Low. Calling loop skips cleanly without exceptions.
- **Pre-Mortem & Prevention:**
  - *Risk:* Summary counters crash if `metrics` is `None`.
  - *Prevention:* Explicit `if metrics is None: continue` guard before any dictionary subscripting.
- **Failing Test to Write First:** `tests/test_analytics_sync_misbinding.py::test_analytics_sync_skips_unpublished_clip`.
- **Data Repair Protocol:** Strictly **REPORT-ONLY**. Read-only verification on the database confirmed 0 affected clips.
- **Rollback Path:** Revert commit on `analytics_sync.py`.

---

#### PLAN-P0-02: Atomic File Writes & Atomic Replace Helper in fs.py
- **Finding IDs:** `F-07`, `F-08`, `F-20`, `F-22`, `F-30`
- **Audit Grade:** Regraded to **C** (Data loss risk under process abort).
- **Evidence Call Sites (4 Persistent State Call Sites):**
  1. `shorts_engine/config/logging_config.py:148-149`: `open(RUNTIME_CONFIG_PATH, "w")` + `json.dump(current, f, indent=2)`
  2. `shorts_engine/services/detection/websub.py:47`: `_SUBS_STATE_FILE.write_text(json.dumps(subs, indent=2))`
  3. `shorts_engine/services/llm/quota_tracker.py:102`: `self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")`
  4. `shorts_engine/services/publishing/quota.py:53`: `self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")`
- **Impact:** **Data Loss / State Corruption.** Sudden process termination during `write_text` truncates state files to 0 bytes.
- **Exact Code Change (Incorporating Amendment):**
  Create leaf utility module `shorts_engine/utils/fs.py` with **zero project imports**:
  ```python
  import os
  import json
  import time
  import logging
  from pathlib import Path
  from typing import Any

  logger = logging.getLogger(__name__)

  def atomic_replace(src: Path, dst: Path, max_retries: int = 5) -> None:
      """Atomically replaces dst with src, retrying on transient Windows file locks."""
      for attempt in range(max_retries):
          try:
              os.replace(src, dst)
              return
          except OSError as e:
              if attempt == max_retries - 1:
                  logger.error("atomic_replace failed from %s to %s: %s", src, dst, e)
                  raise
              time.sleep(0.05 * (2 ** attempt))

  def atomic_write_json(target_path: Path, data: Any, indent: int = 2) -> None:
      """Writes data to a temporary file, flushes/fsyncs, and atomically renames."""
      target_path.parent.mkdir(parents=True, exist_ok=True)
      temp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
      try:
          with open(temp_path, "w", encoding="utf-8") as f:
              json.dump(data, f, indent=indent)
              f.flush()
              os.fsync(f.fileno())
          atomic_replace(temp_path, target_path)
      except OSError as e:
          logger.error("Failed to atomic_write_json to %s: %s", target_path, e)
          if temp_path.exists():
              try:
                  temp_path.unlink()
              except OSError:
                  pass
          raise
  ```
  Replace all 4 persistent-state call sites with `atomic_write_json`.
- **Effort:** Small (S)
- **Risk of Change:** Low. Leaf helper has zero dependencies.
- **Pre-Mortem & Prevention:**
  - *Risk:* Windows `PermissionError` when replacing file held open by reader.
  - *Prevention:* Exponential backoff retry loop (5 retries) catching `OSError`.
- **Failing Test to Write First:** `tests/test_atomic_state_writes.py::test_atomic_write_preserves_original_on_simulated_abort`.
- **Rollback Path:** Revert `shorts_engine/utils/fs.py` and call sites.

---

#### PLAN-P0-03: Resumed Pipeline Transcript Normalization at Load Time
- **Finding ID:** `F-13`
- **Audit Grade:** Regraded to **D** (100% fatal crash on pipeline resumption).
- **Evidence Call Sites:**
  - Crash loop: `shorts_engine/orchestration/runner.py:678-681` (`w.word for w in formatted_transcript.words`)
  - Load site: `shorts_engine/orchestration/runner.py:364-369` (`FormattedTranscript.from_dict(data)`)
  - Model fields: `shorts_engine/services/transcription/formatter.py:8-13` (`word: str`, `start: float`, `end: float`, `probability: float = 1.0`)
- **Impact:** **Critical Crash on Pipeline Resumption.** `w.word` raises `AttributeError: 'dict' object has no attribute 'word'`, crashing the worker and rendering pipeline resumption impossible.
- **Exact Code Change (Strict Normalization):**
  In `shorts_engine/services/transcription/formatter.py`:
  - Enforce strict typing in `WordSegment.from_dict`: no silent 0.0 defaults. Log and skip malformed words or raise `ValueError`.
  - In `FormattedTranscript.from_dict`, support both dictionary payloads (`{"words": [...]}`) and legacy lists (`[{"word": ...}]`).
  ```python
  @classmethod
  def from_dict(cls, data: Any) -> "FormattedTranscript":
      if isinstance(data, list):
          raw_words = data
          language = "en"
          full_text = None
      elif isinstance(data, dict):
          raw_words = data.get("words", [])
          language = data.get("language", "en")
          full_text = data.get("full_text")
      else:
          raise ValueError(f"Invalid transcript data format: {type(data)}")

      words = []
      for w in raw_words:
          if isinstance(w, WordSegment):
              words.append(w)
          elif isinstance(w, dict):
              word = w.get("word")
              start = w.get("start")
              end = w.get("end")
              if word is None or start is None or end is None:
                  logger.warning("Skipping malformed word missing required fields: %s", w)
                  continue
              try:
                  words.append(WordSegment(
                      word=str(word),
                      start=float(start),
                      end=float(end),
                      probability=float(w.get("probability", 1.0)),
                  ))
              except (ValueError, TypeError) as ex:
                  logger.warning("Skipping unparseable word entry %s: %s", w, ex)
                  continue
      return cls(words=words, language=language, full_text=full_text)
  ```
- **Effort:** Small (S)
- **Risk of Change:** Low. Guarantees strictly typed objects across all downstream consumers.
- **Pre-Mortem & Prevention:**
  - *Risk:* Transcript has zero valid words after filtering malformed entries.
  - *Prevention:* If `len(words) == 0` when data was non-empty, log warning and fail-fast with `ValueError`.
- **Failing Test First:** `tests/test_pipeline_resume_transcript_objects.py` (reproduced and verified failing).
- **Rollback Path:** Revert `formatter.py`.

---

#### PLAN-P0-06: Clip Recovery Auto-Fixes & Decoupling from Rendering (Merged with P1-01)
- **Finding IDs:** `F-31`, `F-50`
- **Audit Grade:** Regraded to **D** (Uncallable auto-fix due to `TypeError` and `FileExistsError`; architectural cycle).
- **Evidence Call Sites:** `shorts_engine/services/quality_gate/recovery.py:256, 260-263, 344, 347-353, 357-360`
- **Exact Code Change (Incorporating Amendment):**
  1. **Dependency Injection Only:** `quality_gate` must NEVER import `rendering`. Inject `trimmer_service: Optional[Any] = None` into `ClipRecoveryService.__init__`.
  2. Use the single shared timeout helper from `P1-02` for FFmpeg execution. Delete the separate formula.
  3. Replace raw renames with `atomic_replace` from `shorts_engine/utils/fs.py`.
  4. **Include P2-10:** Safely unlink previous `clip.rendered_path` file on successful re-render to prevent disk orphan accumulation.
  5. Confirm `trim_clip` semantics: atomically re-trims media, generates ASS subtitles, renders 9:16 portrait video, and updates clip record in database.
- **Effort:** Medium (M)
- **Risk of Change:** Medium. Requires updating callers of `ClipRecoveryService` to pass trimmer delegate.
- **Pre-Mortem & Prevention:**
  - *Risk:* Caller invokes recovery fix without supplying trimmer delegate.
  - *Prevention:* Explicit check raising `RuntimeError("Trimmer service required for clip recovery auto-fix")`.
- **Failing Test to Write First:** `tests/test_clip_recovery_auto_fix.py::test_caption_safe_rerender_and_technical_clamp_execute_without_error`.
- **Rollback Path:** Revert `recovery.py`.

---

#### PLAN-P1-02: Enforce Subprocess Timeouts on All Media Probes
- **Finding IDs:** `F-15`, `F-33`, `F-36`, `F-37`
- **Confirmed Dependencies:** `psutil>=5.9.0` declared in `pyproject.toml:37` and `requirements.txt:28`.
- **Evidence Call Sites (5 Unprotected Sites + technical_check.py:84):**
  1. `shorts_engine/services/quality_gate/technical_check.py:38`: `subprocess.run(cmd, ...)` (ffprobe validation)
  2. `shorts_engine/services/quality_gate/technical_check.py:84`: Add timeout error handling to technical check evaluation
  3. `shorts_engine/services/rendering/compositor.py:451`: `subprocess.run(cmd, ...)` (ffprobe render check)
  4. `shorts_engine/services/rendering/thumbnail_generator.py:50`: `subprocess.run(cmd, ...)` (ffmpeg frame extraction)
  5. `shorts_engine/services/audio/mastering.py:117`: `subprocess.run(cmd, ...)` (ffmpeg loudnorm analysis)
  6. `shorts_engine/services/quality_gate/recovery.py:256`: `subprocess.run(cmd, ...)` (ffmpeg duration clamp)
- **Exact Code Change (Unified Timeout Helper):**
  Define a single timeout helper in `shorts_engine/utils/process.py`:
  ```python
  def run_supervised_cmd(cmd: List[str], timeout_sec: float = 30.0, **kwargs) -> subprocess.CompletedProcess:
      """Executes subprocess command with strict timeout and psutil tree termination."""
      try:
          return subprocess.run(cmd, timeout=timeout_sec, **kwargs)
      except subprocess.TimeoutExpired:
          logger.error("Command timed out after %.1fs: %s", timeout_sec, " ".join(cmd[:4]))
          raise
  ```
  Replace all unprotected calls with `run_supervised_cmd`:
  - `ffprobe` probes: 30.0s.
  - `ffmpeg` frame extraction: 30.0s.
  - `ffmpeg` media processing: `max(60.0, float(media_duration) * 3.0)`.
- **Effort:** Small (S)
- **Risk of Change:** Low. Standardizes subprocess error handling.
- **Pre-Mortem & Prevention:**
  - *Risk:* 30s timeout prematurely aborts large file analysis on overloaded systems.
  - *Prevention:* 30s is 300x normal ffprobe run time (~100ms).
- **Failing Test to Write First:** `tests/test_subprocess_timeout_enforcement.py::test_ffprobe_times_out_on_hung_process`.
- **Rollback Path:** Revert timeout calls.

---

#### PLAN-P1-04: Align pyproject.toml Dependencies
- **Finding ID:** `F-03`
- **Evidence:** `pyproject.toml:15-38` vs `requirements.txt`
- **Exact Code Change:** Add `"groq>=0.9.0"` and `"scipy>=1.11.0"` to `pyproject.toml`.
- **Effort:** Small (S)
- **Risk of Change:** Very Low.
- **Pre-Mortem & Prevention:**
  - *Risk:* Dependency resolver conflicts.
  - *Prevention:* Exact versions match tested `requirements.txt`.
- **Failing Test to Write First:** `tests/test_packaging_dependencies.py::test_pyproject_toml_contains_all_core_requirements`.
- **Rollback Path:** Revert `pyproject.toml`.

---

#### PLAN-P1-06: Atomic Lifecycle State Transitions
- **Finding ID:** `F-12`
- **Evidence:** `shorts_engine/orchestration/lifecycle.py:26-36, 47-56`
- **Exact Code Change:** Use atomic SQL conditional update:
  `UPDATE source_videos SET status=:new WHERE id=:id AND status=:expected`.
- **Effort:** Small (S)
- **Risk of Change:** Low. Prevents worker double-processing.
- **Pre-Mortem & Prevention:**
  - *Risk:* In-memory object status diverges from database.
  - *Prevention:* Execute `self.db.refresh(video)` on success.
- **Failing Test to Write First:** `tests/test_lifecycle_concurrency.py::test_concurrent_transition_only_one_succeeds`.
- **Rollback Path:** Revert `lifecycle.py`.

---

#### PLAN-P1-08: Enforce Production Secret Placeholder Guard in Settings
- **Finding ID:** `F-09`
- **Evidence:** `shorts_engine/config/settings.py:41-48, 354-379`
- **Exact Code Change (Incorporating Amendment):**
  1. Add explicit feature switch `ENFORCE_SECRET_ENTROPY: bool = Field(default=False)`.
  2. In `PipelineSettings.validate_bounds`:
     ```python
     if self.ENFORCE_SECRET_ENTROPY:
         if self.ENV.lower() not in ("development", "test"):
             # Reject default placeholders and verify minimum entropy
             ...
     ```
  3. **Launchers must NOT set `ENV=development` for real runs.** The real production fix is: rotate keys with `rotate-secrets`, then set `ENV=production` and `ENFORCE_SECRET_ENTROPY=true`.
- **Effort:** Small (S)
- **Risk of Change:** Low when gated behind feature switch.
- **Pre-Mortem & Prevention:**
  - *Risk:* Accidental premature enforcement crashes production boot.
  - *Prevention:* Switch defaults to `False` until operator rotates secrets.
- **Failing Test to Write First:** `tests/test_settings_security_guard.py::test_production_rejects_placeholder_secrets`.
- **Rollback Path:** Revert `settings.py`.

---

#### PLAN-P1-11: Dynamic Analytics Date Window Calculation
- **Finding ID:** `F-14`
- **Evidence:** `shorts_engine/services/ab_testing/analytics_ingestion.py:113-114`
- **Exact Code Change (Incorporating Amendment):**
  1. Window starts at the clip's publish date:
     ```python
     if clip and clip.published_at:
         start_d = clip.published_at.astimezone(timezone.utc).date()
     else:
         start_d = (datetime.now(timezone.utc) - timedelta(days=30)).date()
     end_d = datetime.now(timezone.utc).date()
     ```
  2. Use timezone-aware UTC (`datetime.now(timezone.utc)`).
- **Effort:** Small (S)
- **Risk of Change:** Low. Must ship before 2027-01-01.
- **Pre-Mortem & Prevention:**
  - *Risk:* API rejects future `endDate`.
  - *Prevention:* Timezone-aware UTC date ensures `endDate` is current UTC day.
- **Failing Test to Write First:** `tests/test_analytics_date_window.py::test_analytics_window_is_dynamic`.
- **Rollback Path:** Revert `analytics_ingestion.py`.

---

#### PLAN-P1-12: Cross-Process File Lock / Counter on Quota State
- **Finding IDs:** `F-22`, `F-30` (Extended)
- **Evidence Call Sites:** `shorts_engine/services/publishing/quota.py:92-98`, `shorts_engine/services/llm/quota_tracker.py:99-105`
- **Worker Process Analysis:** In production, Uvicorn runs 1 web process, and `worker.runner` runs 1 worker process. Manual operator jobs or scheduled CLI scripts run additional concurrent processes. Therefore, in-memory `threading.Lock` is insufficient.
- **Exact Code Change:**
  Implement cross-process file locking in `shorts_engine/utils/fs.py` (`FileLock` using `portalocker` or an atomic lock file with `os.replace`):
  Wrap state read-modify-write cycles in both `shorts_engine/services/publishing/quota.py` and `shorts_engine/services/llm/quota_tracker.py` with the file lock.
- **Effort:** Small (S)
- **Risk of Change:** Low. Prevents concurrent write races across processes.
- **Pre-Mortem & Prevention:**
  - *Risk:* Stale lock file after process crash.
  - *Prevention:* Lock file includes PID and timestamp; stale locks (>10s) are automatically broken.
- **Failing Test to Write First:** `tests/test_quota_concurrency_lock.py::test_concurrent_record_publish_accumulates_correctly`.
- **Rollback Path:** Revert `quota.py` and `quota_tracker.py`.

---

### Priority 0 & Priority 1: Tier 2 Remediation Items (Database Backup First)

#### PLAN-P1-09: Fail-Fast Schema Migrations & Explicit Index Management
- **Finding ID:** `F-11`
- **Evidence Call Site:** `shorts_engine/db/session.py:67-104`
- **Exact Code Change (Incorporating Amendment):**
  1. In `ensure_schema_upgrades`, run the duplicate pre-flight check before creating index:
     ```sql
     SELECT youtube_video_id, COUNT(*) FROM source_videos GROUP BY youtube_video_id HAVING COUNT(*) > 1;
     ```
  2. **Duplicate Rule:** If duplicates exist, **do NOT create the unique index**, log an **ERROR listing all duplicate video IDs**, and **continue booting cleanly**. `PLAN-P0-04` stays blocked awaiting user decision.
  3. If 0 duplicates exist, create unique index `uq_source_videos_youtube_id` on `source_videos(youtube_video_id)`.
  4. Add `publishing_heartbeat_at TIMESTAMP` and ensure `status` enum support for `PUBLISHING` and `FAILED_NEEDS_REVIEW` (for `PLAN-P0-05`).
  5. Remove blanket `except Exception: pass`; raise `RuntimeError` on unexpected SQL failures.
- **Effort:** Small (S)
- **Risk of Change:** Medium.
- **Pre-Mortem & Prevention:**
  - *Risk:* Startup crash if database is locked.
  - *Prevention:* Set `PRAGMA busy_timeout = 5000`.
- **Failing Test to Write First:** `tests/test_schema_migrations.py::test_ensure_schema_upgrades_raises_on_actual_sql_failure`.
- **Rollback Path:** Revert `session.py`.

---

#### PLAN-P0-04: Ingestion Deduplication Mutex & Constraint
- **Finding ID:** `F-16`
- **Evidence Call Site:** `shorts_engine/services/detection/detector.py:36-78`
- **Prerequisite:** Blocked until `PLAN-P1-09` verifies 0 duplicates and creates index.
- **Exact Code Change:** Handle `IntegrityError` from unique constraint cleanly:
  ```python
  try:
      video = SourceVideo(youtube_video_id=video_id, channel_id=channel.id, status=SourceVideoStatus.DETECTED)
      self.db.add(video)
      self.db.commit()
      return video
  except IntegrityError:
      self.db.rollback()
      return self.db.query(SourceVideo).filter_by(youtube_video_id=video_id).first()
  ```
- **Effort:** Small (S)
- **Failing Test to Write First:** `tests/test_detection_concurrency.py::test_concurrent_detection_creates_single_record`.
- **Rollback Path:** Revert `detector.py`.

---

#### PLAN-P0-05: Drip-Feed Publishing Row Lock, PlaylistItems Upload Check & Stale Reclaim (Re-rated P1)
- **Finding ID:** `F-27`
- **Evidence Call Site:** `shorts_engine/services/publishing/drip_feed.py:45-101`
- **Exact Code Change (Incorporating Amendment):**
  1. **Upload Started Record:** Write an "upload started" record in `PublishLog` before starting upload.
  2. **PlaylistItems Upload Check:** Query the channel's uploads playlist (`playlistItems.list(playlistId=uploads_playlist_id)`) via YouTube Data API.
  3. **Clip-ID Token Matching:** Match clips by embedding a unique token `[clip:<clip_id>]` in the YouTube video description.
  4. **Heartbeat Reclaim:** Update `publishing_heartbeat_at` during upload; reclaim clips stuck in `PUBLISHING` >30 minutes.
  5. **Manual Review Route:** Route ambiguous cases to manual review (`ClipStatus.FAILED_NEEDS_REVIEW`).
  6. **Test Specification:** Test with an unlisted upload.
- **Effort:** Medium (M)
- **Risk of Change:** Medium.
- **Pre-Mortem & Prevention:**
  - *Risk:* API failure during upload check triggers redundant upload.
  - *Prevention:* Fail-safe: if upload check fails, do not re-upload; requeue for retry.
- **Failing Test to Write First:** `tests/test_drip_feed_stale_reclaim.py::test_publishing_claim_and_idempotency_guard`.
- **Rollback Path:** Revert `drip_feed.py`.

---

#### PLAN-P1-10: Harden Secret Rotation with Automated Backup and Secure Input
- **Finding ID:** `F-10`
- **Evidence Call Sites:** `shorts_engine/db/security.py:88-114`, `cli.py:44-66`
- **Exact Code Change:**
  1. Trigger automated database backup before rotation.
  2. Read keys from environment variables or `typer.prompt(hide_input=True)`. Remove CLI arguments.
  3. Single atomic transaction; roll back on any error.
- **Effort:** Small (S)
- **Failing Test to Write First:** `tests/test_secret_rotation.py::test_rotate_secrets_creates_backup_and_rolls_back_on_error`.
- **Rollback Path:** Revert `cli.py` and `security.py`.

---

#### PLAN-P1-03: Resumable Upload Chunk Retry with Exponential Backoff
- **Finding ID:** `F-26`
- **Evidence Call Site:** `shorts_engine/services/publishing/client.py:79-88`
- **Exact Code Change:**
  Wrap `request.next_chunk()` in an exponential backoff loop (3 retries). Retry transient 5xx errors; fast-fail on `quotaExceeded`, 401, or 403.
- **Effort:** Small (S)
- **Failing Test to Write First:** `tests/test_youtube_resumable_upload.py::test_retry_transient_error_and_fail_fast_on_quota_exceeded`.
- **Rollback Path:** Revert `client.py`.

---

#### PLAN-P1-05: Harden GitHub Actions CI Workflow
- **Finding ID:** `F-06`
- **Evidence:** `.github/workflows/ci.yml:1-109`
- **Exact Code Change:** Add `timeout-minutes: 15`. Add `pip-audit` as a separate non-blocking commit.
- **Effort:** Small (S)
- **Failing Test to Write First:** Actionlint schema validation.
- **Rollback Path:** Revert `ci.yml`.

---

#### PLAN-P1-07: Scoped Database Sessions in Multi-Platform Syndicator
- **Finding ID:** `F-29`
- **Evidence:** `shorts_engine/services/publishing/manager.py:180-195`
- **Exact Code Change:** Prove shared session with test first; instantiate scoped per-thread sessions.
- **Effort:** Small (S)
- **Failing Test to Write First:** `tests/test_multi_platform_sessions.py::test_syndicator_threads_do_not_share_session`.
- **Rollback Path:** Revert `manager.py`.

---

#### PLAN-P2-11: Enforce 50MB File Size Guard and Chunked Streaming in Instagram Publisher
- **Finding ID:** `F-28`
- **Evidence:** `shorts_engine/services/publishing/instagram.py:121-130`
- **Exact Code Change:** Explicit `if file_size > 50 * 1024 * 1024: raise ValueError(...)`; use chunked streaming in `httpx.post`.
- **Effort:** Small (S)
- **Failing Test to Write First:** `tests/test_instagram_upload.py::test_instagram_rejects_files_over_50mb`.
- **Rollback Path:** Revert `instagram.py`.

---

### Priority 0 & Priority 2: Approved with Change Items

#### PLAN-P0-07: Secure Distribution Bundle Packaging
- **Finding ID:** `F-48`
- **Evidence:** `Shorts_Engine_Control_Center/04_Admin_and_Distribution/build_bundle.bat:30`
- **Exact Code Change (Incorporating Amendment):**
  Exclude: `client_secret*.json`, `credentials*.json`, `token*.json`, `*cookie*.txt`, `.env*`, `*.key`, `*.pem`, `storage/*.db`, `storage/backups/*`.
  **Explicit Allow Rule:** Allow `.env.example`.
  Add zip inspection test.
- **Effort:** Small (S)
- **Failing Test to Write First:** `tests/test_bundle_packaging.py::test_bundle_zip_strictly_excludes_env_and_keys`.
- **Rollback Path:** Revert `build_bundle.bat`.

---

#### PLAN-P2-12: Mask Password Input via getpass in CLI User Creation
- **Finding ID:** `F-49`
- **Evidence:** `cli.py:398-402`, `create_user.bat:37, 59`
- **Exact Code Change:** Make `--password` optional with warning; fall back to `getpass.getpass()`; support `DASHBOARD_BOOTSTRAP_PASSWORD` env var.
- **Effort:** Small (S)
- **Failing Test to Write First:** `tests/test_cli_user_create.py::test_create_user_prompts_with_getpass_when_password_omitted`.
- **Rollback Path:** Revert `cli.py` and `create_user.bat`.

---

## 4. Deferred Items Catalog

| Plan ID | Finding ID | Module & Description | Rationale for Deferral |
| :--- | :--- | :--- | :--- |
| **PLAN-P2-01** | `F-41..47` | Control Center batch script portable paths (`%~dp0..`) | Tooling enhancement; current paths work on primary workstation. |
| **PLAN-P2-02** | `F-51` | Combined single-pass FFmpeg Media QA filtergraph | Performance optimization; two-pass check currently functions correctly. |
| **PLAN-P2-04** | `F-18` | Advance RSS poller cursor on skipped Shorts | Edge-case cursor optimization for Shorts-only channels. |
| **PLAN-P2-05** | `F-19` | Interruptible poller event sleep (`threading.Event`) | Clean shutdown enhancement during polling sleep. |
| **PLAN-P2-06** | `F-21` | Attach bot-bypass cookies to downloader metadata probe | Edge-case metadata probe optimization; download stage already attaches cookies. |
| **PLAN-P2-07** | `F-24` | Non-blocking async/threadpool webhook dispatch | Worker thread latency optimization during webhook alerts. |
| **PLAN-P2-08** | `F-32, 34` | OpenCV `cap.release()` inside `try...finally` | Resource leak guardrail under unhandled exceptions. |
| **PLAN-P2-09** | `F-35` | Font file fallback and container DejaVu packaging | Linux container styling fallback; Windows workstation has fonts installed. |
| **PLAN-P3-01** | `F-01, 02` | Clean git index & consolidate documentation files | Repository hygiene and documentation cleanup. |
| **PLAN-P3-02** | `F-23` | Remove shadowed dead code `remove_key` in quota tracker | Code cleanliness; second definition works as intended. |
| **PLAN-P3-03** | `F-38` | Enforce or deprecate `min_silence_gap_sec` in trimmer | Method signature cleanup. |
| **BENCH-F40**  | `F-40` | Forced aligner acoustic precision benchmark | Heuristic aligner is functional; empirical CTC comparison deferred. |

---

## 5. Accepted Risks Register

| Finding ID | Source Module & Unit / Location | Audit Grade | Finding Description | Accepted Rationale & Operational Mitigations |
| :--- | :--- | :---: | :--- | :--- |
| **F-04** | `QA_VERIFICATION_PROTOCOL_previous version.txt:1-279` | **B+** | Historical verification protocol kept in project root. | Static documentation kept for historical audit continuity; zero runtime execution impact. |
| **F-05** | `.github/workflows/ci-cd.yml:1-117` | **B** | Deployment stage contains placeholder echo commands. | Staging/production deployment infrastructure is not yet provisioned; active CI validation is fully governed by `ci.yml` (hardened under `PLAN-P1-05`). |
| **F-17** | `shorts_engine/services/detection/filter.py:13-41` | **B** | Duration-only check flags short 16:9 landscape videos as Shorts. | Intentional design for Shorts repurposing MVP; full aspect-ratio detection scheduled for post-MVP. |
| **F-40** | `shorts_engine/services/transcription/aligner.py:14-53` | **B** | `ForcedAligner` uses heuristic interval smoothing rather than acoustic CTC forced alignment. | **Assumed:** The ±50ms precision was not empirically measured against audio phonemes. Deferred benchmark test will compare Whisper timestamps with ground-truth wav2vec2 CTC alignment. |
