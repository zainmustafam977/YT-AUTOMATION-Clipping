-- Migration 003 Rollback: Phase 1 Publishing Studio
-- Drops created tables and columns (SQLite 3.35+ supports DROP COLUMN)

DROP TABLE IF EXISTS pending_oauth_mismatches;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS quota_ledger;
DROP TABLE IF EXISTS publish_jobs;
DROP TABLE IF EXISTS oauth_states;
DROP TABLE IF EXISTS channel_credentials;

-- Remove clips draft columns
ALTER TABLE clips DROP COLUMN draft_title;
ALTER TABLE clips DROP COLUMN draft_description;
ALTER TABLE clips DROP COLUMN draft_tags;
ALTER TABLE clips DROP COLUMN draft_category_id;
ALTER TABLE clips DROP COLUMN draft_privacy_status;
ALTER TABLE clips DROP COLUMN draft_made_for_kids;
ALTER TABLE clips DROP COLUMN draft_target_channel_id;
ALTER TABLE clips DROP COLUMN draft_scheduled_at;
ALTER TABLE clips DROP COLUMN draft_version;
ALTER TABLE clips DROP COLUMN source;

-- Remove channels extensions
ALTER TABLE channels DROP COLUMN title;
ALTER TABLE channels DROP COLUMN handle;
ALTER TABLE channels DROP COLUMN avatar_url;
ALTER TABLE channels DROP COLUMN last_verified_at;
