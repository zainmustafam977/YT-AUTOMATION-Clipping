-- Migration: 002_m3_postgres_scale.sql
-- Description: Milestone 3 PostgreSQL 16 schema, users table, indexes, and M3-01 automated updated_at triggers

-- 1. Create Users Table & UserRole Type
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'userrole') THEN
        CREATE TYPE userrole AS ENUM ('ADMIN', 'OPERATOR', 'VIEWER');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'OPERATOR',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);

-- 2. Ensure Channel OAuth Encrypted Column Exists
ALTER TABLE channels ADD COLUMN IF NOT EXISTS encrypted_oauth_token TEXT;

-- 3. M3-01 Automated Timestamp Trigger Function
CREATE OR REPLACE FUNCTION m3_01_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = (NOW() AT TIME ZONE 'utc');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4. Apply M3-01 Triggers to Core Tables
DROP TRIGGER IF EXISTS m3_01_channels_update ON channels;
CREATE TRIGGER m3_01_channels_update
    BEFORE UPDATE ON channels
    FOR EACH ROW
    EXECUTE FUNCTION m3_01_update_timestamp();

DROP TRIGGER IF EXISTS m3_01_source_videos_update ON source_videos;
CREATE TRIGGER m3_01_source_videos_update
    BEFORE UPDATE ON source_videos
    FOR EACH ROW
    EXECUTE FUNCTION m3_01_update_timestamp();

DROP TRIGGER IF EXISTS m3_01_clips_update ON clips;
CREATE TRIGGER m3_01_clips_update
    BEFORE UPDATE ON clips
    FOR EACH ROW
    EXECUTE FUNCTION m3_01_update_timestamp();

DROP TRIGGER IF EXISTS m3_01_users_update ON users;
CREATE TRIGGER m3_01_users_update
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION m3_01_update_timestamp();
