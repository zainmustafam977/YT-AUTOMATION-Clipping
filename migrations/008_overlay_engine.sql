-- E-011 — multi-layer overlay engine
CREATE TABLE IF NOT EXISTS overlay_preset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(128) NOT NULL UNIQUE,
    description TEXT,
    spec_json TEXT NOT NULL,           -- serialized OverlaySpec
    spec_sha256 VARCHAR(64) NOT NULL,  -- tamper detection
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(64),
    is_builtin INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_overlay_preset_name ON overlay_preset(name);

CREATE TABLE IF NOT EXISTS overlay_render_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER,             -- FK to clip_candidates
    preset_id INTEGER,                -- FK to overlay_preset
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    ok INTEGER NOT NULL DEFAULT 0,    -- 1=ok 0=failed
    error TEXT,
    ffmpeg_cmd TEXT,
    output_path TEXT,
    contrast_ratio REAL,              -- WCAG ratio of first text layer
    bytes_written INTEGER
);
CREATE INDEX IF NOT EXISTS ix_overlay_render_audit_candidate ON overlay_render_audit(candidate_id);
CREATE INDEX IF NOT EXISTS ix_overlay_render_audit_preset ON overlay_render_audit(preset_id);
