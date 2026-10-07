-- Migration 003: Phase 1 Publishing Studio, Multi-Channel OAuth & Quota Ledger
-- SQLite & PostgreSQL compatible DDL

-- 1. Channels table extensions
ALTER TABLE channels ADD COLUMN title VARCHAR(255);
ALTER TABLE channels ADD COLUMN handle VARCHAR(255);
ALTER TABLE channels ADD COLUMN avatar_url VARCHAR(1024);
ALTER TABLE channels ADD COLUMN last_verified_at DATETIME;

-- Populate channels.title with channels.name for backwards compatibility
UPDATE channels SET title = name WHERE title IS NULL AND name IS NOT NULL;

-- 2. Channel Credentials table (UNIQUE per channel_id)
CREATE TABLE IF NOT EXISTS channel_credentials (
    id VARCHAR(36) PRIMARY KEY,
    channel_id VARCHAR(36) NOT NULL UNIQUE,
    encrypted_refresh_token TEXT NOT NULL,
    nonce VARCHAR(64),
    scopes TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    verified_at DATETIME,
    last_error TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_credentials_channel_id ON channel_credentials(channel_id);

-- 3. OAuth States (Single-use, 10-minute expiry)
CREATE TABLE IF NOT EXISTS oauth_states (
    state VARCHAR(128) PRIMARY KEY,
    pkce_verifier VARCHAR(256) NOT NULL,
    intended_channel_id VARCHAR(64),
    expected_handle VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_oauth_states_expires_at ON oauth_states(expires_at);

-- 4. Pending OAuth Mismatches (temporary holding for operator resolution)
CREATE TABLE IF NOT EXISTS pending_oauth_mismatches (
    id VARCHAR(36) PRIMARY KEY,
    intended_channel_id VARCHAR(64),
    authorized_channel_id VARCHAR(64) NOT NULL,
    authorized_title VARCHAR(255),
    authorized_handle VARCHAR(255),
    authorized_avatar_url VARCHAR(1024),
    encrypted_token_data TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL
);

-- 5. Publish Jobs (state machine: DRAFT -> SCHEDULED -> UPLOADING -> PUBLISHED / PAUSED_NEEDS_ATTENTION / FAILED / CANCELLED)
CREATE TABLE IF NOT EXISTS publish_jobs (
    id VARCHAR(36) PRIMARY KEY,
    clip_id VARCHAR(36) NOT NULL,
    target_youtube_channel_id VARCHAR(64) NOT NULL,
    title VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    tags TEXT,
    category_id VARCHAR(16) NOT NULL DEFAULT '22',
    privacy_status VARCHAR(16) NOT NULL DEFAULT 'unlisted',
    made_for_kids BOOLEAN NOT NULL DEFAULT 0,
    schedule_mode VARCHAR(16) NOT NULL DEFAULT 'native',
    scheduled_at DATETIME,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    attempts INTEGER NOT NULL DEFAULT 0,
    youtube_video_id VARCHAR(64),
    upload_session_uri TEXT,
    last_error TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(clip_id) REFERENCES clips(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_publish_jobs_clip_id ON publish_jobs(clip_id);
CREATE INDEX IF NOT EXISTS ix_publish_jobs_status ON publish_jobs(status);
CREATE INDEX IF NOT EXISTS ix_publish_jobs_scheduled_at ON publish_jobs(scheduled_at);
CREATE INDEX IF NOT EXISTS ix_publish_jobs_youtube_video_id ON publish_jobs(youtube_video_id);

-- 6. Quota Ledger (Append-only)
CREATE TABLE IF NOT EXISTS quota_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    units INTEGER NOT NULL,
    operation VARCHAR(64) NOT NULL,
    channel_id VARCHAR(64),
    job_id VARCHAR(36)
);
CREATE INDEX IF NOT EXISTS ix_quota_ledger_ts ON quota_ledger(ts);

-- 7. Audit Log (Append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor VARCHAR(64) NOT NULL DEFAULT 'system',
    event VARCHAR(64) NOT NULL,
    channel_id VARCHAR(64),
    job_id VARCHAR(36),
    details TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_log_ts ON audit_log(ts);

-- 8. Clips table draft metadata and optimistic concurrency extensions
ALTER TABLE clips ADD COLUMN draft_title VARCHAR(100);
ALTER TABLE clips ADD COLUMN draft_description TEXT;
ALTER TABLE clips ADD COLUMN draft_tags TEXT;
ALTER TABLE clips ADD COLUMN draft_category_id VARCHAR(16);
ALTER TABLE clips ADD COLUMN draft_privacy_status VARCHAR(16);
ALTER TABLE clips ADD COLUMN draft_made_for_kids BOOLEAN;
ALTER TABLE clips ADD COLUMN draft_target_channel_id VARCHAR(64);
ALTER TABLE clips ADD COLUMN draft_scheduled_at DATETIME;
ALTER TABLE clips ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE clips ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'pipeline';
