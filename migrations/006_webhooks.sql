-- E-008 + E-009 + E-010 + E-016/017 + EASE-02/E-021
CREATE TABLE IF NOT EXISTS webhooks (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    url VARCHAR(1024) NOT NULL,
    secret_hash VARCHAR(64) NOT NULL,
    events_json TEXT NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_by VARCHAR(64) NOT NULL DEFAULT 'system',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_webhooks_is_active ON webhooks(is_active);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id VARCHAR(36) PRIMARY KEY,
    webhook_id VARCHAR(36) NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    http_status INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wd_webhook_id ON webhook_deliveries(webhook_id);
CREATE INDEX IF NOT EXISTS ix_wd_event ON webhook_deliveries(event);
CREATE INDEX IF NOT EXISTS ix_wd_status ON webhook_deliveries(status);
CREATE INDEX IF NOT EXISTS ix_wd_created_at ON webhook_deliveries(created_at);

CREATE TABLE IF NOT EXISTS webhook_dead_letters (
    id VARCHAR(36) PRIMARY KEY,
    webhook_id VARCHAR(36) NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event VARCHAR(64) NOT NULL,
    body TEXT NOT NULL,
    last_status INTEGER,
    error TEXT,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wdl_webhook_id ON webhook_dead_letters(webhook_id);
CREATE INDEX IF NOT EXISTS ix_wdl_created_at ON webhook_dead_letters(created_at);

CREATE TABLE IF NOT EXISTS settings_exports (
    id VARCHAR(36) PRIMARY KEY,
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_by VARCHAR(64) NOT NULL DEFAULT 'system',
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_entries (
    id VARCHAR(36) PRIMARY KEY,
    job_id VARCHAR(64),
    clip_id VARCHAR(36) REFERENCES clips(id) ON DELETE CASCADE,
    kind VARCHAR(32) NOT NULL,
    units FLOAT NOT NULL DEFAULT 0,
    unit_cost_usd FLOAT NOT NULL DEFAULT 0,
    total_cost_usd FLOAT NOT NULL DEFAULT 0,
    actor VARCHAR(64),
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ce_clip_id ON cost_entries(clip_id);
CREATE INDEX IF NOT EXISTS ix_ce_kind ON cost_entries(kind);
CREATE INDEX IF NOT EXISTS ix_ce_created_at ON cost_entries(created_at);

CREATE TABLE IF NOT EXISTS cost_budgets (
    id VARCHAR(36) PRIMARY KEY,
    scope VARCHAR(32) NOT NULL DEFAULT 'global',
    scope_id VARCHAR(64),
    soft_limit_usd FLOAT NOT NULL DEFAULT 10.0,
    hard_limit_usd FLOAT NOT NULL DEFAULT 20.0,
    period VARCHAR(16) NOT NULL DEFAULT 'daily',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cb_scope ON cost_budgets(scope);

CREATE TABLE IF NOT EXISTS ab_tests_2 (
    id VARCHAR(36) PRIMARY KEY,
    clip_id VARCHAR(36) NOT NULL UNIQUE REFERENCES clips(id) ON DELETE CASCADE,
    metric VARCHAR(16) NOT NULL DEFAULT 'ctr',
    status VARCHAR(16) NOT NULL DEFAULT 'RUNNING',
    rotation_seconds INTEGER NOT NULL DEFAULT 21600,
    winner_variant_id VARCHAR(36),
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_abt2_status ON ab_tests_2(status);

CREATE TABLE IF NOT EXISTS ab_variants_2 (
    id VARCHAR(36) PRIMARY KEY,
    test_id VARCHAR(36) NOT NULL REFERENCES ab_tests_2(id) ON DELETE CASCADE,
    title VARCHAR(100) NOT NULL,
    description VARCHAR(5000),
    tags TEXT,
    is_control BOOLEAN NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    last_published_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_abv2_test_id ON ab_variants_2(test_id);

CREATE TABLE IF NOT EXISTS ab_metric_snapshots_2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id VARCHAR(36) NOT NULL REFERENCES ab_tests_2(id) ON DELETE CASCADE,
    variant_id VARCHAR(36) NOT NULL REFERENCES ab_variants_2(id) ON DELETE CASCADE,
    captured_at TIMESTAMP NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    ctr FLOAT NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS ix_abs2_test_id ON ab_metric_snapshots_2(test_id);
CREATE INDEX IF NOT EXISTS ix_abs2_variant_id ON ab_metric_snapshots_2(variant_id);
CREATE INDEX IF NOT EXISTS ix_abs2_captured_at ON ab_metric_snapshots_2(captured_at);

CREATE TABLE IF NOT EXISTS saved_views (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    expression TEXT NOT NULL DEFAULT '',
    owner VARCHAR(64) NOT NULL DEFAULT 'system',
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_guides (
    id VARCHAR(36) PRIMARY KEY,
    slug VARCHAR(128) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    "order" INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_og_slug ON operator_guides(slug);
