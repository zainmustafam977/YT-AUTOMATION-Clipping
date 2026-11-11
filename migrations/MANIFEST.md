# Migration Manifest

Migrations are applied in numeric order. Each `NNN_*.sql` has a matching `*_rollback.sql`.

## Order
1. (missing) `001_initial_schema.sql` — baseline tables (referenced but not present; reconstruct from `shorts_engine/db/models.py`).
2. `002_m3_postgres_scale.sql`
3. `003_phase1_publishing_studio.sql` (+ rollback)
4. (missing) `004_*` — gap in numbering.
5. `005_batch_engine.sql` (+ rollback)
6. `006_webhooks.sql` (+ rollback)
7. `007_caption_editor.sql` (+ rollback)
8. `008_overlay_engine.sql` (+ rollback)
9. `009_saved_views_user_id.sql` (+ rollback)

## Notes
- Gaps 001 and 004 are tracked as tech debt; `Base.metadata.create_all` covers fresh installs.
- Apply via the project's migration runner in `shorts_engine/db/migrations.py` (if present) or manually in order.