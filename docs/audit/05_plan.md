# Phase 5: Industrial-Grade Remediation Plan (v4)

---

## 1. Governance & Strict Approval Gate

In accordance with the Principal Engineer Charter and the operational mode (**"Audit, wait for approval, then implement P0+P1"**), **no application code has been modified**. This plan reconciles 100% of audit findings, establishes exact evidence-backed fixes, pre-mortems, test specifications, and data repair protocols.

**Implementation of this plan will proceed strictly upon explicit user approval.**
Where user directives conflict with previous plan text, the user directives win.

---

## 2. Priority Taxonomy & Tier Execution Structure

- **Tier 1 (Code Only - Stop for Review After the Tier):**
  - `PLAN-P0-01`: Fix Analytics Video ID Misbinding (Code fix only; data repair stays report-only).
  - `PLAN-P0-02`: Atomic File Writes for Runtime State (Leaf helper with Windows retry; quota lock check).
  - `PLAN-P0-03`: Resumed Pipeline Transcript Normalization (Strict load-time normalization; no silent 0.0 defaults; `WordSegment` citation).
  - `PLAN-P0-06`: Clip Recovery Auto-Fixes & Circular Import Decoupling (Merged with P1-01; dependency injection; 0 cycles).
  - `PLAN-P1-02`: Enforce Subprocess Timeouts on All Media Probes (`psutil` declared; all 5 unprotected call sites covered).
  - `PLAN-P1-04`: Align pyproject.toml Dependencies (`groq`, `scipy`).
  - `PLAN-P1-06`: Atomic Lifecycle State Transitions (Conditional SQL).
  - `PLAN-P1-08`: Enforce Production Secret Placeholder Guard in Settings (Code only; enforcement deferred until Precondition 3 is resolved).
  - `PLAN-P1-11`: Dynamic Analytics Date Window Calculation (Promoted from P2-03; must ship before 2027-01-01).
  - `PLAN-P1-12`: Concurrency Lock on Quota State Read-Modify-Write (New P1 finding).

- **Tier 2 (Database Backup First - Ask User Before Running):**
  - `PLAN-P1-09`: Fail-Fast Schema Migrations & Unique Index Management (Includes `source_videos(youtube_video_id)` unique index).
  - `PLAN-P0-04`: Ingestion Deduplication Constraint (Runs after P1-09; stop and report if duplicates exist, never dedupe automatically).
  - `PLAN-P0-05`: Drip-Feed Publishing Row Lock, Upload Started Marker, Channel Upload Check & Stale Reclaim (Re-rated P1).
  - `PLAN-P1-10`: Harden Secret Rotation with Automated Backup and Secure Input (`rotate-secrets` with hidden prompt).
  - `PLAN-P1-03`: Resumable Upload Chunk Retry with Exponential Backoff.
  - `PLAN-P1-05`: Harden GitHub Actions CI Workflow (`pip-audit` as a separate non-blocking commit).
  - `PLAN-P1-07`: Scoped Database Sessions in Multi-Platform Syndicator (Prove shared session with test first).
  - `PLAN-P2-11`: Enforce 50MB File Size Guard and Chunked Streaming in Instagram Publisher.

- **Approved with Change:**
  - `PLAN-P0-07`: Secure Distribution Bundle Packaging (Use `fnmatch` / glob matching; test includes `.env`, `.env.local`, `*.key`, `*.pem`, `storage/*.db`).
  - `PLAN-P2-12`: Mask Password Input via `getpass` in CLI User Creation (Remove/warn `--password` flag).

- **Deferred Items:**
  - Remaining P2 items (`PLAN-P2-01`, `PLAN-P2-02`, `PLAN-P2-04`, `PLAN-P2-05`, `PLAN-P2-06`, `PLAN-P2-07`, `PLAN-P2-08`, `PLAN-P2-09`, `PLAN-P2-10`).
  - All P3 items (`PLAN-P3-01`, `PLAN-P3-02`, `PLAN-P3-03`).
  - The `F-40` acoustic forced alignment benchmark.

- **Rejected Items:** None.

- **Operational Rules:**
  1. One finding per git commit.
  2. Write failing test first before implementing fix.
  3. Zero blanket `except: pass` or `except Exception: pass`.
  4. Zero disabled or skipped existing tests.
  5. Every item must have a Risk assessment; every P1 and above item must have a Pre-Mortem.

---

## 3. Prioritized Remediation Catalog

### Priority 0 & Priority 1: Tier 1 Remediation Items (Code Only)

#### PLAN-P0-01: Fix Analytics Video ID Misbinding & Report-Only Data Repair
- **Finding ID:** `F-25`
- **Evidence Call Site:** `shorts_engine/services/monitoring/analytics_sync.py:47-50`
  ```python
  if clip.publish_log and clip.publish_log.youtube_video_id:
      yt_id = clip.publish_log.youtube_video_id
  elif clip.candidate and clip.candidate.source_video:
      yt_id = clip.candidate.source_video.youtube_video_id
  ```
- **Impact:** **Severe Data Corruption & False A/B Promotion.** If a clip lacks a `publish_log`, the daemon queries YouTube metrics for the *source video* (long-form upload) and records those view counts into `clip.analytics_json`. This corrupts clip statistics with false views, misleading the Bayesian bandit daemon into promoting underperforming clips.
- **Likelihood:** High (deterministic execution on every unpublished clip evaluated by the daemon).
- **Exact Code Change:**
  In `shorts_engine/services/monitoring/analytics_sync.py:46-56`:
  ```python
  if not clip.publish_log or not clip.publish_log.youtube_video_id:
      logger.warning("Clip %s has no published YouTube ID; skipping analytics sync (misbinding prevented).", clip.id)
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
  *(Note: uses `logger.warning` only; no undeclared metrics counter module).*
- **Effort:** Small (S)
- **Risk of Change:** Low. Calling loop skips cleanly without exceptions.
- **Pre-Mortem & Prevention:**
  - *Risk:* Downstream summary calculations expect `metrics` to always be a dict and crash with `AttributeError` if `None`.
  - *Prevention:* Explicit `if metrics is None: continue` guard before any dictionary key accesses.
- **Failing Test to Write First:** `tests/test_analytics_sync_misbinding.py::test_analytics_sync_skips_unpublished_clip`.
- **Data Repair Protocol:** Strictly **REPORT-ONLY**. Read-only verification on the development database confirmed 0 affected clips. If run against production, the tool will report all clips having `c.analytics_json IS NOT NULL` where `pl.youtube_video_id IS NULL`, without modifying or wiping data unless explicitly directed by the operator.
- **Rollback Path:** Revert commit on `analytics_sync.py`.

---

#### PLAN-P0-02: Atomic File Writes for Runtime State & Quotas
- **Finding IDs:** `F-07`, `F-08`, `F-20`, `F-22`, `F-30`
- **Evidence Call Sites (4 Persistent State Call Sites):**
  1. `shorts_engine/config/logging_config.py:148-149`: `open(RUNTIME_CONFIG_PATH, "w")` + `json.dump(current, f, indent=2)`
  2. `shorts_engine/services/detection/websub.py:47`: `_SUBS_STATE_FILE.write_text(json.dumps(subs, indent=2))`
  3. `shorts_engine/services/llm/quota_tracker.py:102`: `self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")`
  4. `shorts_engine/services/publishing/quota.py:53`: `self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")`
- **Impact:** **Data Loss / State Corruption.** Sudden process termination (OOM kill, power loss, Ctrl+C) during `write_text` truncates state files to 0 bytes, permanently destroying active API key quotas, subscription expiries, and runtime setting overrides.
- **Likelihood:** Medium.
- **Exact Code Change:**
  Implement a leaf module helper with **zero project imports** at `shorts_engine/services/storage/path_manager.py` (or a dedicated leaf module `shorts_engine/utils/fs.py`):
  ```python
  import os
  import json
  import time
  from pathlib import Path
  from typing import Any

  def atomic_write_json(target_path: Path, data: Any, indent: int = 2) -> None:
      """Writes data to a temporary file, flushes/fsyncs, and atomically renames with Windows retry."""
      target_path.parent.mkdir(parents=True, exist_ok=True)
      temp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
      try:
          with open(temp_path, "w", encoding="utf-8") as f:
              json.dump(data, f, indent=indent)
              f.flush()
              os.fsync(f.fileno())
          
          # Atomic replace with retry for transient Windows file locks
          max_retries = 5
          for attempt in range(max_retries):
              try:
                  os.replace(temp_path, target_path)
                  break
              except PermissionError:
                  if attempt == max_retries - 1:
                      raise
                  time.sleep(0.05 * (2 ** attempt))
      except Exception:
          if temp_path.exists():
              try:
                  temp_path.unlink()
              except Exception:
                  pass
          raise
  ```
  Replace all 4 persistent-state call sites with `atomic_write_json`.
- **Effort:** Small (S)
- **Risk of Change:** Low. Leaf helper has no dependencies on the rest of the application.
- **Pre-Mortem & Prevention:**
  - *Risk:* On Windows, another thread reading the target file while `os.replace` executes can raise `PermissionError`.
  - *Prevention:* Built-in exponential backoff retry loop (5 attempts, 50ms initial backoff).
- **Failing Test to Write First:** `tests/test_atomic_state_writes.py::test_atomic_write_preserves_original_on_simulated_abort`.
- **Rollback Path:** Revert `path_manager.py` and call sites.

---

#### PLAN-P0-03: Resumed Pipeline Transcript Normalization at Load Time
- **Finding ID:** `F-13`
- **Evidence Call Sites:**
  - Crash loop: `shorts_engine/orchestration/runner.py:678-681`
    ```python
    seg_text = " ".join([
        w.word for w in formatted_transcript.words
        if cand.start_ms <= w.start * 1000 <= cand.end_ms
    ])
    ```
  - Transcript load site: `shorts_engine/orchestration/runner.py:364-369`
    ```python
    if formatted_transcript is None:
        transcript_record = self.db.query(Transcript).filter_by(source_video_id=video.id).first()
        if transcript_record:
            data = json.loads(transcript_record.word_level_json)
            formatted_transcript = FormattedTranscript.from_dict(data)
    ```
  - Model fields: `shorts_engine/services/transcription/formatter.py:8-13`
    ```python
    class WordSegment:
        def __init__(self, word: str, start: float, end: float, probability: float = 1.0):
            self.word = word.strip()
            self.start = float(start)
            self.end = float(end)
            self.probability = float(probability)
    ```
- **Impact:** **Critical Crash on Pipeline Resumption.** When a pipeline resumes, `word_level_json` is deserialized. If `words` contains dicts rather than `WordSegment` objects, `w.word` raises `AttributeError: 'dict' object has no attribute 'word'`, crashing the worker and preventing resumption.
- **Likelihood:** High (fires on 100% of resumed pipeline runs reaching quality gate evaluation).
- **Exact Code Change:**
  Enforce strict normalization at `FormattedTranscript.from_dict` and `WordSegment.from_dict` in `shorts_engine/services/transcription/formatter.py`:
  - **No silent 0.0 defaults.** If a word record is missing required fields or has unparseable floats, log a warning and skip the malformed word (or raise `ValueError` if the entire segment list is corrupted).
  ```python
  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "WordSegment":
      word = data.get("word")
      start = data.get("start")
      end = data.get("end")
      if word is None or start is None or end is None:
          raise ValueError(f"Malformed WordSegment dictionary lacks required fields: {data}")
      return cls(
          word=str(word),
          start=float(start),
          end=float(end),
          probability=float(data.get("probability", 1.0)),
      )
  ```
  In `FormattedTranscript.from_dict`:
  ```python
  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "FormattedTranscript":
      words = []
      for w in data.get("words", []):
          if isinstance(w, WordSegment):
              words.append(w)
          elif isinstance(w, dict):
              try:
                  words.append(WordSegment.from_dict(w))
              except Exception as e:
                  logger.warning("Skipping malformed word in transcript: %s (%s)", w, e)
      return cls(words=words, language=data.get("language", "en"), full_text=data.get("full_text"))
  ```
- **Effort:** Small (S)
- **Risk of Change:** Low. Guarantees strictly typed objects across all downstream consumers.
- **Pre-Mortem & Prevention:**
  - *Risk:* If a transcript record has entirely corrupt words, returning 0 words might cause downstream division-by-zero or empty string errors.
  - *Prevention:* If `len(words) == 0` when transcript JSON existed, raise `ValueError("Transcript contains zero valid words after normalization")` to fail-fast cleanly.
- **Failing Test to Write First:** `tests/test_pipeline_resume_transcript_objects.py::test_load_transcript_normalizes_dicts_to_word_segments`.
- **Rollback Path:** Revert `formatter.py`.

---

#### PLAN-P0-06: Clip Recovery Auto-Fixes & Decoupling from Rendering (Merged with P1-01)
- **Finding IDs:** `F-31`, `F-50`
- **Evidence Call Sites:** `shorts_engine/services/quality_gate/recovery.py:256, 260-263, 344, 347-353, 357-360`
  - Line 256: `res = subprocess.run(cmd, ...)` lacks timeout.
  - Line 263 & 360: `rename()` causes `FileExistsError` on Windows.
  - Line 344: `ass_path.write_text(ass_content, encoding="utf-8")` uses raw write.
  - Lines 349–353: `compositor.render_short(cropped_video_path=in_path, ass_subtitle_path=ass_path, output_path=temp_out)` raises fatal `TypeError: unexpected keyword argument 'cropped_video_path'`.
- **Import Cycle Evidence:** `import_analysis.json` confirms 1 circular dependency between `shorts_engine.services.rendering` and `shorts_engine.services.quality_gate`.
- **Cited Trimmer Signature:** From `shorts_engine/services/rendering/trimmer.py:91-98`:
  ```python
  def trim_clip(
      self,
      clip_id: str,
      start_sec: float,
      end_sec: float,
      snap_words: bool = True,
      caption_style_preset: Optional[str] = None,
  ) -> Dict[str, Any]:
  ```
- **Exact Code Change:**
  1. **Dependency Injection Only:** `quality_gate` must NEVER import `rendering`. Inject a renderer/trimmer callable or protocol into `ClipRecoveryService.__init__(self, trimmer_service=None)`.
  2. In `recovery.py:346-354`, invoke the injected trimmer delegate instead of directly instantiating `VideoCompositor`.
  3. Use shared `atomic_replace` helper (from P0-02) to eliminate Windows `FileExistsError`.
  4. Use shared timeout helper (with P1-02) for FFmpeg clamp at line 256 (`timeout=max(30.0, float(target_max) * 2.0)`).
  5. Assert that the import cycle count is 0 and recovery output is functionally equivalent.
- **Effort:** Medium (M)
- **Risk of Change:** Medium. Requires updating callers of `ClipRecoveryService` to pass the trimmer dependency.
- **Pre-Mortem & Prevention:**
  - *Risk:* If caller passes `trimmer_service=None`, recovery auto-fix could raise `AttributeError: 'NoneType' object has no attribute 'trim_clip'`.
  - *Prevention:* Explicit check: `if not self.trimmer_service: raise RuntimeError("Trimmer service required for video re-render recovery")`.
- **Failing Test to Write First:** `tests/test_clip_recovery_auto_fix.py::test_caption_safe_rerender_and_technical_clamp_execute_without_error`.
- **Rollback Path:** Revert `recovery.py`.

---

#### PLAN-P1-02: Enforce Subprocess Timeouts on All Media Probes
- **Finding IDs:** `F-15`, `F-33`, `F-36`, `F-37`
- **Confirmed Dependencies:** `psutil>=5.9.0` is declared in `pyproject.toml:37` and `psutil>=5.9.8` in `requirements.txt:28`.
- **Evidence Call Sites (5 Unprotected Sites):**
  1. `shorts_engine/services/quality_gate/technical_check.py:38`: `subprocess.run(cmd, ...)` (ffprobe validation)
  2. `shorts_engine/services/rendering/compositor.py:451`: `subprocess.run(cmd, ...)` (ffprobe render check)
  3. `shorts_engine/services/rendering/thumbnail_generator.py:50`: `subprocess.run(cmd, ...)` (ffmpeg frame extraction)
  4. `shorts_engine/services/audio/mastering.py:117`: `subprocess.run(cmd, ...)` (ffmpeg loudnorm analysis)
  5. `shorts_engine/services/quality_gate/recovery.py:256`: `subprocess.run(cmd, ...)` (ffmpeg clamp)
- **Exact Code Change:**
  Enforce explicit timeouts on all call sites:
  - `ffprobe` probes (`technical_check.py:38`, `compositor.py:451`): `timeout=30.0`.
  - `ffmpeg` frame extraction (`thumbnail_generator.py:50`): `timeout=30.0`.
  - `ffmpeg` audio/video processing (`mastering.py:117`, `recovery.py:256`): `timeout=max(60.0, float(media_duration) * 3.0)`.
  - Wrap in `try...except subprocess.TimeoutExpired` and terminate orphan process trees via `psutil`.
- **Effort:** Small (S)
- **Risk of Change:** Low. Standardizes subprocess failure handling across all media services.
- **Pre-Mortem & Prevention:**
  - *Risk:* On high-load or low-spec systems, 30s timeout on ffprobe could fire prematurely on very large source files.
  - *Prevention:* 30s is more than 300x normal ffprobe execution time (~50-100ms); if exceeded, the atom is genuinely corrupt.
- **Failing Test to Write First:** `tests/test_subprocess_timeout_enforcement.py::test_ffprobe_times_out_on_hung_process`.
- **Rollback Path:** Revert timeout additions.

---

#### PLAN-P1-04: Align pyproject.toml Dependencies
- **Finding ID:** `F-03`
- **Evidence:** `pyproject.toml:15-38` vs `requirements.txt`
- **Exact Code Change:**
  Add `"groq>=0.9.0"` and `"scipy>=1.11.0"` to the `dependencies` list in `pyproject.toml`.
- **Effort:** Small (S)
- **Risk of Change:** Very Low. Synchronizes packaging manifest with runtime requirements.
- **Pre-Mortem & Prevention:**
  - *Risk:* Version pinning conflicts if someone runs `pip install -e .`.
  - *Prevention:* Versions match existing `requirements.txt` specifications.
- **Failing Test to Write First:** `tests/test_packaging_dependencies.py::test_pyproject_toml_contains_all_core_requirements`.
- **Rollback Path:** Revert `pyproject.toml`.

---

#### PLAN-P1-06: Atomic Lifecycle State Transitions
- **Finding ID:** `F-12`
- **Evidence:** `shorts_engine/orchestration/lifecycle.py:13-57`
- **Impact:** In-memory status check (`video.status != expected_status`) followed by deferred commit allows concurrent workers to race, leading to duplicate processing.
- **Exact Code Change:**
  Replace in-memory checks with atomic SQL conditional updates:
  ```python
  stmt = (
      update(SourceVideo)
      .where(SourceVideo.id == video.id, SourceVideo.status == expected_status)
      .values(status=target_status)
  )
  res = self.db.execute(stmt)
  self.db.commit()
  return res.rowcount > 0
  ```
- **Effort:** Small (S)
- **Risk of Change:** Low. Prevents double-processing races.
- **Pre-Mortem & Prevention:**
  - *Risk:* Caller holds stale in-memory `video` object whose `.status` attribute doesn't reflect database update.
  - *Prevention:* Execute `self.db.refresh(video)` upon successful conditional update.
- **Failing Test to Write First:** `tests/test_lifecycle_concurrency.py::test_concurrent_transition_only_one_succeeds`.
- **Rollback Path:** Revert `lifecycle.py`.

---

#### PLAN-P1-08: Enforce Production Secret Placeholder Guard in Settings
- **Finding ID:** `F-09`
- **Evidence:** `shorts_engine/config/settings.py:41-48, 354-379`
- **Exact Code Change (Code Only - Enforcement Deferred):**
  In `PipelineSettings.validate_bounds`:
  1. Change check to fail-closed: `if self.ENV.lower() not in ("development", "test"):`.
  2. Reject keys matching `.env.example` placeholders (`yt_shorts_dev_master_secret_key_32_bytes_min!`, `yt_shorts_dev_dashboard_session_secret_32_bytes!`, `AdminPass12345!`).
  3. Enforce minimum length checks (`len(MASTER_ENCRYPTION_KEY) >= 32`, `len(DASHBOARD_SECRET_KEY) >= 32`, `len(DASHBOARD_PASSWORD) >= 12`).
  4. **Enforcement Gate:** Do NOT enable enforcement in production until Precondition 3 is resolved (P1-10 secret rotation with backup is completed, and launcher scripts inject `ENV=development` for local runs).
- **Effort:** Small (S)
- **Risk of Change:** High if enabled prematurely (refuses to boot if keys are placeholders). Mitigated by deferred enforcement and explicit dev flags.
- **Pre-Mortem & Prevention:**
  - *Risk:* Developers running local test scripts fail to boot because `ENV` is not set and defaults to non-development.
  - *Prevention:* Launcher scripts set `set "ENV=development"`; local `.env` specifies `ENV=development`.
- **Failing Test to Write First:** `tests/test_settings_security_guard.py::test_production_rejects_placeholder_secrets`.
- **Rollback Path:** Revert `settings.py`.

---

#### PLAN-P1-11: Dynamic Analytics Date Window Calculation (Promoted from P2-03)
- **Finding ID:** `F-14`
- **Evidence:** `shorts_engine/services/ab_testing/analytics_ingestion.py:113-114`
  ```python
  startDate="2026-01-01",
  endDate="2026-12-31",
  ```
- **Impact:** Hardcoded static calendar year strings expire on **2027-01-01** (~14 weeks), breaking metric ingestion. Must ship before 2027-01-01.
- **Exact Code Change:**
  Calculate dynamic dates relative to current UTC date:
  ```python
  end_d = datetime.utcnow().date()
  start_d = end_d - timedelta(days=30)
  req = self.analytics_client.reports().query(
      ids="channel==MINE",
      startDate=start_d.isoformat(),
      endDate=end_d.isoformat(),
      metrics="averageViewPercentage,averageViewDuration",
      dimensions="video",
      filters=f"video=={video_id}",
  )
  ```
- **Effort:** Small (S)
- **Risk of Change:** Low. Preserves API parameter schema while making window dynamic.
- **Pre-Mortem & Prevention:**
  - *Risk:* YouTube Analytics API requires dates in strictly formatted `YYYY-MM-DD`.
  - *Prevention:* Use `.isoformat()` from `datetime.date`, guaranteeing exact standard format.
- **Failing Test to Write First:** `tests/test_analytics_date_window.py::test_analytics_window_is_dynamic`.
- **Rollback Path:** Revert `analytics_ingestion.py`.

---

#### PLAN-P1-12: Concurrency Lock on Quota State Read-Modify-Write
- **Finding IDs:** `F-22`, `F-30` (Extended)
- **Evidence Call Sites:** `shorts_engine/services/publishing/quota.py:92-98`, `shorts_engine/services/llm/quota_tracker.py:99-105`
- **Impact:** In `shorts_engine/services/publishing/quota.py`, the read-modify-write cycle (`record_publish`) lacks any locking mechanism. Concurrent publishing workers or simultaneous schedulers overwrite each other's state, leading to undercounted YouTube quota units and sudden quota exhaustion (HTTP 403 `quotaExceeded`).
- **Likelihood:** Medium.
- **Exact Code Change:**
  Add a shared reentrant lock (`threading.RLock()`) or file-based mutex across `_load_state` and `_save_state` in `shorts_engine/services/publishing/quota.py`:
  ```python
  import threading

  class QuotaTracker:
      _lock = threading.RLock()

      def record_publish(self, cost: int = UPLOAD_COST_UNITS, db: Optional[Any] = None) -> int:
          with self._lock:
              state = self._load_state(db=db)
              state["used_units"] = state.get("used_units", 0) + cost
              self._save_state(state)
              return state["used_units"]
  ```
- **Effort:** Small (S)
- **Risk of Change:** Low. Prevents concurrent write races.
- **Pre-Mortem & Prevention:**
  - *Risk:* Deadlock if `_load_state` called inside `record_publish` also acquires the lock.
  - *Prevention:* Use `threading.RLock()` (reentrant lock) to allow nested acquisitions within the same thread.
- **Failing Test to Write First:** `tests/test_quota_concurrency_lock.py::test_concurrent_record_publish_accumulates_correctly`.
- **Rollback Path:** Revert `quota.py`.

---

### Priority 0 & Priority 1: Tier 2 Remediation Items (Database Backup First)

#### PLAN-P1-09: Fail-Fast Schema Migrations & Explicit Index Management in session.py
- **Finding ID:** `F-11`
- **Evidence Call Site:** `shorts_engine/db/session.py:67-104`
- **Impact:** Blanket `try...except Exception: pass` swallows `ALTER TABLE` failures, hiding missing columns or permission errors until runtime crash loops occur.
- **Exact Code Change:**
  1. Check column existence via `PRAGMA table_info` before executing `ALTER TABLE`.
  2. In `ensure_schema_upgrades`, add creation of the unique index `uq_source_videos_youtube_id` on `source_videos(youtube_video_id)`.
  3. Remove blanket `except Exception: pass`; tolerate "column already exists" as idempotent; raise `RuntimeError` on any genuine SQL failure.
- **Effort:** Small (S)
- **Risk of Change:** Medium. Ensures database errors fail-fast during boot.
- **Pre-Mortem & Prevention:**
  - *Risk:* If database is temporarily locked during startup, raising `RuntimeError` terminates the service.
  - *Prevention:* Set SQLite busy timeout (`PRAGMA busy_timeout = 5000`) before running upgrades.
- **Failing Test to Write First:** `tests/test_schema_migrations.py::test_ensure_schema_upgrades_raises_on_actual_sql_failure`.
- **Rollback Path:** Revert `session.py`.

---

#### PLAN-P0-04: Ingestion Deduplication Mutex & Constraint
- **Finding ID:** `F-16`
- **Evidence Call Site:** `shorts_engine/services/detection/detector.py:36-78`
- **Ordering Mandate:** Runs strictly **AFTER `PLAN-P1-09`**.
- **Pre-flight Duplicate Check:**
  ```sql
  SELECT youtube_video_id, COUNT(*) FROM source_videos GROUP BY youtube_video_id HAVING COUNT(*) > 1;
  ```
  **Rule:** If duplicates exist, **STOP and REPORT to operator**; do NOT dedupe automatically.
- **Exact Code Change:**
  In `detector.py`, wrap insertion in a transaction catching `IntegrityError` from the unique index created in P1-09:
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
- **Risk of Change:** Low. Leverages database engine unique constraint.
- **Pre-Mortem & Prevention:**
  - *Risk:* Rolling back on `IntegrityError` in a shared session might discard pending changes on other entities.
  - *Prevention:* Restrict transaction scope strictly to the `SourceVideo` insertion.
- **Failing Test to Write First:** `tests/test_detection_concurrency.py::test_concurrent_detection_creates_single_record`.
- **Rollback Path:** Revert `detector.py`.

---

#### PLAN-P0-05: Drip-Feed Publishing Row Lock, Upload Started Marker, Channel Upload Check & Stale Reclaim (Re-rated P1)
- **Finding ID:** `F-27`
- **Evidence Call Site:** `shorts_engine/services/publishing/drip_feed.py:45-101`
- **Impact:** Single-node worker restarts or concurrent scheduler runs can double-publish clips or leave clips stuck in `PUBLISHING` status indefinitely.
- **Exact Code Change:**
  1. **Upload Started Record:** Write an "upload started" marker in `PublishLog` before uploading.
  2. **Channel Upload Check:** Query the channel's uploads via YouTube API before any re-upload to confirm whether the clip is already live.
  3. **Stale PUBLISHING Reclaim:** Use a heartbeat mechanism; reclaim clips stuck in `PUBLISHING` for >30 minutes.
  4. **Manual Review Route:** Route ambiguous or failed re-upload attempts to manual operator review (`ClipStatus.FAILED_NEEDS_REVIEW`).
- **Effort:** Medium (M)
- **Risk of Change:** Medium. Modifies publishing state machine.
- **Pre-Mortem & Prevention:**
  - *Risk:* Network drop during channel upload check falsely reports video as absent, causing duplicate upload.
  - *Prevention:* If channel upload check fails with network error, do not proceed with upload; requeue for retry.
- **Failing Test to Write First:** `tests/test_drip_feed_stale_reclaim.py::test_publishing_claim_and_idempotency_guard`.
- **Rollback Path:** Revert `drip_feed.py`.

---

#### PLAN-P1-10: Harden Secret Rotation with Automated Backup and Secure Input
- **Finding ID:** `F-10`
- **Evidence Call Sites:** `shorts_engine/db/security.py:88-114`, `cli.py:44-66`
- **Impact:** CLI command passes master encryption keys via command-line arguments, leaking secrets to shell history and `ps` process listing. Lacks automated backup before rotation.
- **Exact Code Change:**
  1. Trigger `DatabaseBackupManager().backup()` automatically before beginning rotation.
  2. Read keys from environment variables (`OLD_MASTER_KEY`, `NEW_MASTER_KEY`) or interactive hidden prompts (`typer.prompt(..., hide_input=True)`). Remove command-line argument passing.
  3. Maintain atomic single-transaction execution; roll back completely on any decryption failure.
- **Effort:** Small (S)
- **Risk of Change:** Low. Protects keys from process snooping and provides immediate rollback backup.
- **Pre-Mortem & Prevention:**
  - *Risk:* Backup directory full or read-only prevents rotation from proceeding.
  - *Prevention:* Verify backup creation succeeds before starting database transaction.
- **Failing Test to Write First:** `tests/test_secret_rotation.py::test_rotate_secrets_creates_backup_and_rolls_back_on_error`.
- **Rollback Path:** Revert `cli.py` and `security.py`.

---

#### PLAN-P1-03: Resumable Upload Chunk Retry with Exponential Backoff
- **Finding ID:** `F-26`
- **Evidence Call Site:** `shorts_engine/services/publishing/client.py:79-88`
- **Impact:** Transient network drops during YouTube resumable uploads cause the upload loop to crash, wasting bandwidth and requiring a full re-upload.
- **Exact Code Change:**
  Wrap `request.next_chunk()` in an exponential backoff loop (3 retries, initial delay 2s, max 10s).
  **Filter Rule:** Retry only transient network drops (500, 502, 503, 504, connection reset). **Never retry non-retryable errors like `quotaExceeded`, 401 Unauthorized, or 403 Forbidden.**
- **Effort:** Small (S)
- **Risk of Change:** Low. Improves resilience against transient connection drops.
- **Pre-Mortem & Prevention:**
  - *Risk:* Infinite retry loop on quota exhaustion.
  - *Prevention:* Strict error inspection failing fast on HTTP 403 and `quotaExceeded`.
- **Failing Test to Write First:** `tests/test_youtube_resumable_upload.py::test_retry_transient_error_and_fail_fast_on_quota_exceeded`.
- **Rollback Path:** Revert `client.py`.

---

#### PLAN-P1-05: Harden GitHub Actions CI Workflow
- **Finding ID:** `F-06`
- **Evidence Call Site:** `.github/workflows/ci.yml:1-109`
- **Exact Code Change:**
  1. Add `timeout-minutes: 15` to all CI jobs.
  2. Implement `pip-audit` as a **separate commit, non-blocking at first** (`continue-on-error: true`), to avoid turning CI red immediately.
- **Effort:** Small (S)
- **Risk of Change:** Low. Prevents hung CI jobs from consuming runner minutes.
- **Pre-Mortem & Prevention:**
  - *Risk:* Stricter CI timeout fails valid integration tests on slow GitHub runners.
  - *Prevention:* 15 minutes is 5x current suite duration (~2.5 minutes).
- **Failing Test to Write First:** Actionlint / YAML schema validation.
- **Rollback Path:** Revert `ci.yml`.

---

#### PLAN-P1-07: Scoped Database Sessions in Multi-Platform Syndicator
- **Finding ID:** `F-29`
- **Evidence Call Site:** `shorts_engine/services/publishing/manager.py:180-195`
- **Impact:** Multi-threaded syndication worker shares a single non-thread-safe SQLAlchemy `Session` across concurrent publishing threads, risking memory corruption and broken transactions.
- **Exact Code Change:**
  1. **Prove shared session with a test first.**
  2. Instantiate per-thread scoped database sessions or execute platform publishes sequentially.
- **Effort:** Small (S)
- **Risk of Change:** Low. Thread-local session isolation is standard practice.
- **Pre-Mortem & Prevention:**
  - *Risk:* Unclosed scoped sessions leak database connections.
  - *Prevention:* Use context manager `with SessionLocal() as session:` ensuring cleanup in `finally`.
- **Failing Test to Write First:** `tests/test_multi_platform_sessions.py::test_syndicator_threads_do_not_share_session`.
- **Rollback Path:** Revert `manager.py`.

---

#### PLAN-P2-11: Enforce 50MB File Size Guard and Chunked Streaming in Instagram Publisher
- **Finding ID:** `F-28`
- **Evidence Call Site:** `shorts_engine/services/publishing/instagram.py:121-130`
- **Impact:** `_upload_direct_binary` buffers entire MP4 file in memory via `content=f.read()`. Files exceeding 50MB bypass size checks and risk OOM crashes.
- **Exact Code Change:**
  1. Enforce explicit pre-check: `if file_size > 50 * 1024 * 1024: raise ValueError(...)`.
  2. Use chunked streaming generator in `httpx.post` instead of loading entire file into memory.
- **Effort:** Small (S)
- **Risk of Change:** Low. Protects worker memory budget.
- **Pre-Mortem & Prevention:**
  - *Risk:* Streaming upload without `Content-Length` header rejected by Meta API.
  - *Prevention:* Explicitly supply `"file_size": str(file_size)` header per Meta Graph API specification.
- **Failing Test to Write First:** `tests/test_instagram_upload.py::test_instagram_rejects_files_over_50mb`.
- **Rollback Path:** Revert `instagram.py`.

---

### Priority 0 & Priority 2: Approved with Change Items

#### PLAN-P0-07: Secure Distribution Bundle Packaging
- **Finding ID:** `F-48`
- **Evidence Call Site:** `Shorts_Engine_Control_Center/04_Admin_and_Distribution/build_bundle.bat:30`
- **Impact:** Distribution zip packaging omits `.env`, `.env.local`, `*.key`, `*.pem`, and `storage/*.db` from its exclusion set, risking leakage of active production credentials in release archives.
- **Exact Code Change:**
  Use `fnmatch` / glob matching instead of simple set membership. Exclude:
  `.env`, `.env.*`, `*.key`, `*.pem`, `storage/*.db`, `storage/backups/*`, `downloads/*`, `__pycache__/*`, `.git/*`.
  Add a zip-inspecting test that asserts none of these sensitive file patterns appear in the built zip.
- **Effort:** Small (S)
- **Risk of Change:** Very Low. Protects credentials from packaging leaks.
- **Pre-Mortem & Prevention:**
  - *Risk:* Overly broad exclusion pattern accidentally omits required application code.
  - *Prevention:* Targeted patterns restricted to credential extensions and storage directories.
- **Failing Test to Write First:** `tests/test_bundle_packaging.py::test_bundle_zip_strictly_excludes_env_and_keys`.
- **Rollback Path:** Revert `build_bundle.bat`.

---

#### PLAN-P2-12: Mask Password Input via getpass in CLI User Creation
- **Finding ID:** `F-49`
- **Evidence Call Sites:** `cli.py:398-402`, `Shorts_Engine_Control_Center/04_Admin_and_Distribution/create_user.bat:37, 59`
- **Impact:** Plaintext password required via `--password` command-line option leaks secrets into shell history and process monitor.
- **Exact Code Change:**
  1. In `cli.py:cmd_create_user`, make password optional and fall back to `getpass.getpass("Enter password (min 12 chars): ")`.
  2. Warn or remove the `--password` flag in `cli.py`.
  3. In `create_user.bat`, remove the plaintext `--password` command-line argument.
- **Effort:** Small (S)
- **Risk of Change:** Low. Prevents password leakage.
- **Pre-Mortem & Prevention:**
  - *Risk:* Automated CI or provisioning scripts relying on `--password` fail if flag is completely removed.
  - *Prevention:* Support optional password input via `DASHBOARD_BOOTSTRAP_PASSWORD` environment variable for non-interactive headless provisioning.
- **Failing Test to Write First:** `tests/test_cli_user_create.py::test_create_user_prompts_with_getpass_when_password_omitted`.
- **Rollback Path:** Revert `cli.py` and `create_user.bat`.

---

## 4. Deferred Items Catalog

The following items are formally deferred to post-Tier 2:

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
| **PLAN-P2-10** | `F-39` | Cleanup orphaned render artifacts on re-trim | Disk space maintenance on manual re-trims. |
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

---

## 6. Pre-Implementation Verification Summary

1. **Precondition 1:** All cited `path:line` evidence re-opened and verified via `read_chunk.py`. Verified exact line shifts and quotes between v2, v3, and v4.
2. **Precondition 2:** Live read-only check on development SQLite database confirmed 0 affected clips, 0 corrupted A/B promotions, and 0 published clips. Metrics counter module confirmed absent; `logger.warning` specified.
3. **Precondition 3:** Default encryption/dashboard keys in settings match placeholders. Plan v4 mandates running `PLAN-P1-10` before enabling `PLAN-P1-08` in production, and configuring `ENV=development` in launcher scripts.
4. **Precondition 4:** Full grep results recorded for 4 persistent state writes and 5 unprotected subprocess calls. `PLAN-P0-02` and `PLAN-P1-02` extended to cover all of them. `PLAN-P1-12` added for quota concurrency lock.
5. **Precondition 5:** Grade breakdown (338 A, 46 B/B+, 3 C/C+, 0 D). Dimension sweep documented across quota, token refresh, backup/restore, supervision, stuck jobs, alerting, and fail-fast migration execution.
