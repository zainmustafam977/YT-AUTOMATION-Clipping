# Assessment Cards: shorts_engine/services/quality_gate/recovery.py

---

## Unit 1: ClipRecoveryService.__init__ & diagnose_clip
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/recovery.py:30-130` |
| What it does | Initializes `QualityGateManager` and extracts structured diagnostics from `clip.quality_check_result`: breaks down technical, framing, and semantic check failures, aggregates advisories, and generates recommended remediation action descriptors for the UI modal. |
| Industrial-grade? | **A** - Clean diagnostics parser with robust error handling for missing/malformed JSON. |
| Checklist | **Error handling:** Pass (gracefully handles null/corrupt JSON and missing keys). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current diagnostics parser (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: ClipRecoveryService.re_evaluate_clip & force_approve_clip
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/recovery.py:131-220` |
| What it does | `re_evaluate_clip` re-executes all quality gate checks against the rendered video and updates DB status in-place. `force_approve_clip` records an audit trail containing operator username, timestamp, and justification note into `quality_check_result`, bypassing all automated rejections. |
| Industrial-grade? | **A-** - Comprehensive operator override and re-evaluation workflow with audit tracking. |
| Checklist | **Error handling:** Pass (verifies file existence). **Timeouts:** Pass (delegates to QualityGateManager). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (logs operator identifier). **Performance:** Pass. **Observability:** Pass (detailed log of status transitions and operator actions). **Config:** Pass (reads runtime overrides). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Does not use `try...except` around `db.commit()` to perform a rollback if a database exception occurs. |
| Improvement | Wrap database operations in `try/except: db.rollback(); raise`. |
| Better approach? | Option 1: Transaction rollback on failure (Recommended). |
| Priority / Effort | P2 / S |

---

## Unit 3: ClipRecoveryService.apply_auto_fix
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/recovery.py:221-370` |
| What it does | Dispatches automated remediation passes: 1. `technical_clamp` trims MP4 duration to configured limits via FFmpeg stream copy; 2. `reanchor_dialogue` re-anchors hook/payoff text to exact spoken transcript sentences; 3. `caption_safe_rerender` attempts to re-render subtitles with safe margins. |
| Industrial-grade? | **C** - Critical interface mismatches in `caption_safe_rerender` and un-supervised subprocesses in `technical_clamp`. |
| Checklist | **Error handling:** Needs improvement (silent catch blocks, signature mismatches). **Timeouts:** Fail (`technical_clamp` uses bare `subprocess.run` without timeout). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Needs improvement (temp files may leak on error). **Concurrency safety:** Fail (races on in-place file replacement). **Atomicity:** Needs improvement (`out_path.rename(in_path)` fails on Windows if file exists; must use `os.replace`). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (`copy` codec for clamp). **Observability:** Pass. **Config:** Pass. **Testability:** Fail (sub-branch crashes on call). **Readability:** Pass. |
| Weakness or risk | 1. **Critical Defect:** In `caption_safe_rerender` (lines 336-353), `ASSSubtitleGenerator` and `VideoCompositor.render_short` are called with invalid argument signatures (`margin_v`, `cropped_video_path`, `ass_subtitle_path`, `output_path`), which will raise a `TypeError` if invoked. 2. `technical_clamp` uses `subprocess.run` without timeout protection. 3. `out_path.rename(in_path)` causes `FileExistsError` on Windows if destination exists; should use `os.replace`. |
| Improvement | 1. Refactor `caption_safe_rerender` to delegate to `ClipTrimmerService.trim_clip(..., caption_style_preset=...)` which handles full multi-stage rendering correctly. 2. Add `timeout=30.0` or use `SubprocessWatchdog.run` for FFmpeg clamping. 3. Use `os.replace(out_path, in_path)` for atomic file replacement. |
| Better approach? | Option 1: Delegate to `ClipTrimmerService` + atomic `os.replace` (Recommended). Option 2: Mismatched direct calls (Current - broken). |
| Priority / Effort | P0 / M |

---

## Unit 4: ClipRecoveryService.open_in_folder
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/recovery.py:372-409` |
| What it does | Class method that launches native OS file explorer pointing directly to the generated Short video file on Windows (`explorer.exe /select,`), macOS (`open -R`), and Linux (`xdg-open`). |
| Industrial-grade? | **A-** - Ergonomic local desktop workflow supporting all major operating systems. |
| Checklist | **Error handling:** Pass (checks file existence, catches OS launch exceptions). **Timeouts:** N/A (`subprocess.Popen` non-blocking). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (validates file existence). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (OS shell). **Security:** Pass (arg list prevents shell injection). **Performance:** Pass (asynchronous process launch). **Observability:** Pass (returns diagnostic dictionary). **Config:** N/A. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | On Windows, if path contains characters like commas, `explorer /select,<path>` can treat it as separate arguments. |
| Improvement | Sanitize path or pass normalized Windows path string without unescaped comma separators. |
| Better approach? | Option 1: Standardized OS explorer launcher (Recommended). |
| Priority / Effort | P3 / S |
