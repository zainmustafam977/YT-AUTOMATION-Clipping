-- 009 rollback — restore saved_views.owner as a username string
-- Re-derives the name from the foreign key. Views belonging to accounts that
-- were deleted after the migration are already gone by cascade and cannot be
-- recovered. SQLite <3.35 doesn't DROP COLUMN and has no ALTER COLUMN, so on
-- those builds keep user_id as the authoritative column instead.
ALTER TABLE saved_views ADD COLUMN owner VARCHAR(64);

UPDATE saved_views
   SET owner = (SELECT users.username FROM users WHERE users.id = saved_views.user_id);

UPDATE saved_views
   SET owner = 'system'
 WHERE owner IS NULL;

DROP INDEX IF EXISTS ix_saved_views_user_id;

ALTER TABLE saved_views DROP COLUMN user_id;
