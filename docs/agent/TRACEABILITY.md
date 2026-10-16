# TRACEABILITY

This file is generated from `docs/agent/ledger.yaml`.
Hand-editing is forbidden; edit the ledger instead.

| F-ID | Title | Status | Tests | Files |
|------|-------|--------|-------|-------|
| F-01 | Single-process orchestrator, no checkpoints | DONE-VERIFIED | tests/test_worker_recovery.py | shorts_engine/services/worker/runner.py, shorts_engine/services/queue/durable_queue.py |
| F-02 | Local filesystem is the system of record | CONFIRMED | tests/test_storage_path_manager.py | shorts_engine/services/storage/path_manager.py |
| F-03 | Retry in front of irreversible publish, no idempotency | DONE-VERIFIED | tests/test_idempotency.py | shorts_engine/services/queue/durable_queue.py |
| F-04 | OAuth refresh tokens stored in the DB | DONE-VERIFIED | tests/test_oauth_token_encryption.py | shorts_engine/db/security.py |
| F-05 | Untrusted intake run unsandboxed | DONE-VERIFIED | tests/test_url_guard.py | shorts_engine/services/security/url_guard.py, shorts_engine/services/security/policy_gate.py |
| F-06 | No idempotency at the ingestion boundary | DONE-VERIFIED | tests/test_local_ingestion.py | shorts_engine/services/ingestion/local_loader.py |
| F-07 | GPU/CPU contention, no scheduling | DONE-VERIFIED | tests/test_rendering.py | shorts_engine/services/execution/hwaccel.py |
| F-08 | No tracing or metrics | DONE-VERIFIED | tests/test_observability_and_monitoring.py | shorts_engine/services/monitoring/prometheus_metrics.py |
| F-09 | Volatile in-process event bus | DONE-VERIFIED | tests/test_outbox.py | shorts_engine/services/events/bus.py |
| F-10 | LLM clip selection non-deterministic and unmeasured | DONE-VERIFIED | tests/test_clip_production_fixes.py | shorts_engine/services/clip_selection/ |
| F-11 | Quality gate is a human bottleneck | DONE-VERIFIED | tests/test_quality_gate_enhancements.py | shorts_engine/services/quality_gate/ |
| F-12 | Burnt-in-only captions | DONE-VERIFIED | tests/test_caption_emission.py | shorts_engine/services/captioning/ |
| F-13 | A/B title mutations on live posts | DONE-VERIFIED | tests/test_winner_daemon.py | shorts_engine/services/ab_testing/winner_daemon.py |
| F-14 | No backup and recovery discipline | DONE-VERIFIED | tests/test_backup_restore.py | cli.py |
| F-15 | Operator console lacks triage primitives | DONE-VERIFIED | tests/test_triage.py, tests/test_triage_routes.py | shorts_engine/services/triage/__init__.py, shorts_engine/dashboard/routes/needs_attention.py |
| F-16 | No test strategy for non-deterministic stages | DONE-VERIFIED | tests/test_clip_production_fixes.py | shorts_engine/services/telemetry/ |
| F-17 | Rights and provenance unmodelled | DONE-VERIFIED | tests/test_rights_ledger_compliance.py, tests/test_migration_004_compliance_ledger.py | migrations/004_compliance_ledger.sql, shorts_engine/services/rights_ledger/ |
| F-18 | Render output not verified or atomically published | DONE-VERIFIED | tests/test_render_validate.py | shorts_engine/services/rendering/render_inspector.py |
| F-19 | Configuration not schema-validated, no feature flags | DONE-VERIFIED | tests/test_settings_placeholder_guard.py | shorts_engine/config/settings.py |
| F-20 | Per-job cost invisible | DONE-VERIFIED | tests/test_cost_rollup.py | shorts_engine/services/cost/engine.py, shorts_engine/dashboard/routes/cost.py |
| F-21 | Partial platform failures unmodelled | DONE-VERIFIED | tests/test_multi_platform_syndication.py | shorts_engine/services/publishing/multi_platform_syndication.py |
| F-22 | Audio mastering optional and unverified | DONE-VERIFIED | tests/test_audio_mastering.py, tests/test_audio_mastering_real_ffmpeg.py | shorts_engine/services/audio/mastering.py |
| F-23 | Reframing confidence not surfaced | DONE-VERIFIED | tests/test_reframing_confidence.py | shorts_engine/services/reframing/recorder.py |
| F-24 | Single-node deploy, no probes | DONE-VERIFIED | tests/test_drain_endpoint.py, tests/test_deep_health_probes.py | shorts_engine/dashboard/routes/health.py, shorts_engine/services/worker/runner.py |
