-- 009 — saved views: username owner -> users.id foreign key
-- Ownership follows the account, so a rename keeps the views and a delete
-- removes them. Rows whose legacy owner matches no account keep a NULL
-- user_id (unclaimed) rather than being deleted. Applied at boot by
-- ensure_schema_upgrades(); kept here for manual and PostgreSQL runs.
ALTER TABLE saved_views ADD COLUMN user_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE;

UPDATE saved_views
   SET user_id = (SELECT users.id FROM users WHERE users.username = saved_views.owner)
 WHERE user_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_saved_views_user_id ON saved_views (user_id);

-- Requires SQLite >= 3.35 or PostgreSQL.
ALTER TABLE saved_views DROP COLUMN owner;
