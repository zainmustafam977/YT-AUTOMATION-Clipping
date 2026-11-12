-- F-17: rights / licence attestation ledger

CREATE TABLE IF NOT EXISTS rights_attestations (
    id VARCHAR(36) PRIMARY KEY,
    clip_id VARCHAR(36) REFERENCES clips(id) ON DELETE CASCADE,
    source_video_id VARCHAR(36) REFERENCES source_videos(id) ON DELETE CASCADE,
    license_type VARCHAR(32) NOT NULL,
    license_url VARCHAR(1024),
    attestor VARCHAR(128) NOT NULL,
    note TEXT,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT ck_rights_target_present CHECK (clip_id IS NOT NULL OR source_video_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_rights_attestations_clip_id ON rights_attestations(clip_id);
CREATE INDEX IF NOT EXISTS ix_rights_attestations_source_video_id ON rights_attestations(source_video_id);
CREATE INDEX IF NOT EXISTS ix_rights_attestations_license_type ON rights_attestations(license_type);
CREATE INDEX IF NOT EXISTS ix_rights_attestations_created_at ON rights_attestations(created_at);
