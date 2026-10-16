# FINDINGS — Audit Report Status

This file is generated from `docs/agent/ledger.yaml`.
Hand-editing is forbidden; edit the ledger instead.

| Status | Meaning |
|--------|---------|
| CONFIRMED | audit finding present in code; no fix |
| PARTIAL | control in place; gap remains |
| ALREADY-FIXED | control existed and was tested before agent run |
| IN-PROGRESS | code exists; not yet proven by test |
| DONE-VERIFIED | control in place + regression test passes |
| DONE-UNVERIFIED | control in place; only ran on shim |
| BLOCKED | external blocker with documented attempts |
| DEFERRED | 3 failed attempts documented |
| WONT-FIX | out of scope |
| FALSE-POSITIVE | audit claim wrong on inspection |


## Audit findings F-01..F-24 (titles from the audit report)

| ID | Title | Status | Evidence |
|----|-------|--------|----------|
| F-01 | Single-process orchestrator, no checkpoints | **PARTIAL** | services/worker/runner.py is in-process; durable_queue.py persists state; reconcile_stale_claims runs on boot |
| F-02 | Local filesystem is the system of record | **CONFIRMED** | PathManager resolves all paths to STORAGE_DIR; no remote object store integration |
| F-03 | Retry in front of irreversible publish, no idempotency | **DONE-VERIFIED** | durable_queue.py attaches idempotency keys; tests/test_idempotency.py asserts no double-publish |
| F-04 | OAuth refresh tokens stored in the DB | **DONE-VERIFIED** | Fernet AES-128-CBC + HMAC at rest; tests/test_oauth_token_encryption.py |
| F-05 | Untrusted intake run unsandboxed | **PARTIAL** | policy_gate.py and url_guard.py exist; ffmpeg is invoked with explicit args; tests/test_url_guard.py |
| F-06 | No idempotency at the ingestion boundary | **DONE-VERIFIED** | local_loader.py + dedupe hash on youtube_video_id; tests/test_local_ingestion.py |
| F-07 | GPU/CPU contention, no scheduling | **PARTIAL** | services/execution/hwaccel.py prefers GPU but falls back to CPU; runner is FIFO |
| F-08 | No tracing or metrics | **PARTIAL** | prometheus_metrics.py exposes /metrics; correlation_id is in every log line; tests/test_observability_and_monitoring.py |
| F-09 | Volatile in-process event bus | **PARTIAL** | bus.py is in-process pub/sub; jobs persisted to DB via outbox; tests/test_outbox.py |
| F-10 | LLM clip selection non-deterministic and unmeasured | **PARTIAL** | clip_selection/scoring.py and validator.py exist; golden eval set in tests/test_clip_production_fixes.py |
| F-11 | Quality gate is a human bottleneck | **PARTIAL** | services/quality_gate/ has structured rejection reasons; auto-approve on green path; tests/test_quality_gate_enhancements.py |
| F-12 | Burnt-in-only captions | **DONE-VERIFIED** | services/captioning/ass_generator.py emits ASS; SRT/VTT sidecar generators; tests/test_caption_emission.py |
| F-13 | A/B title mutations on live posts | **DONE-VERIFIED** | ab_testing/winner_daemon.py with mode-aware guard; tests/test_winner_daemon.py + mode switch tests |
| F-14 | No backup and recovery discipline | **DONE-VERIFIED** | cli.py:cmd_backup_db, cmd_restore_db; tests/test_backup_restore.py |
| F-15 | Operator console lacks triage primitives | **DONE-VERIFIED** | routes/needs_attention.py + templates/needs_attention.html; tests/test_needs_attention.py |
| F-16 | No test strategy for non-deterministic stages | **PARTIAL** | frozen_clock.py, deterministic_seed.py; tests assert exact output for fixed input |
| F-17 | Rights and provenance unmodelled | **DONE-VERIFIED** | services/rights_ledger/ + migration 004 + mode-aware publish guard; tests/test_rights_ledger_compliance.py |
| F-18 | Render output not verified or atomically published | **DONE-VERIFIED** | render_inspector.py checks output frame; rename-to-final is atomic; tests/test_render_validate.py |
| F-19 | Configuration not schema-validated, no feature flags | **PARTIAL** | pydantic-settings v2 validates env; RIGHTS_LEDGER_MODE and PREREGISTRATION_MODE feature flags; full feature-flag system not built |
| F-20 | Per-job cost invisible | **DONE-VERIFIED** | services/cost/engine.py + dashboard/cost.py + cli cost command; tests/test_cost_rollup.py |
| F-21 | Partial platform failures unmodelled | **PARTIAL** | multi_platform_syndication.py; tests/test_multi_platform_syndication.py |
| F-22 | Audio mastering optional and unverified | **DONE-VERIFIED** | mastering.py runs EBU R128 loudnorm by default; tests/test_audio_mastering.py + tests/test_audio_mastering_real_ffmpeg.py |
| F-23 | Reframing confidence not surfaced | **DONE-VERIFIED** | reframing/recorder.py persists reframing_confidence into clip.quality_check_result JSON; tests/test_reframing_confidence.py |
| F-24 | Single-node deploy, no probes | **DONE-VERIFIED** | routes/health.py exposes /health/live, /health/ready, /health/drain; runner reads drain flag; tests/test_drain_endpoint.py + test_deep_health_probes.py |
