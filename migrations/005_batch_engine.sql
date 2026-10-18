-- E-001 + R3 batch engine: BatchJob, BatchItemLock, BatchAuditLog

CREATE TABLE IF NOT EXISTS batch_jobs (
    id VARCHAR(36) PRIMARY KEY,
    operation VARCHAR(64) NOT NULL,
    total_items INTEGER NOT NULL DEFAULT 0,
    ok_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    actor VARCHAR(64) NOT NULL DEFAULT 'system',
    state VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    params_json TEXT,
    created_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_batch_jobs_operation ON batch_jobs(operation);
CREATE INDEX IF NOT EXISTS ix_batch_jobs_state ON batch_jobs(state);
CREATE INDEX IF NOT EXISTS ix_batch_jobs_created_at ON batch_jobs(created_at);

CREATE TABLE IF NOT EXISTS batch_item_locks (
    id VARCHAR(36) PRIMARY KEY,
    batch_id VARCHAR(36) NOT NULL,
    operation VARCHAR(64) NOT NULL,
    item_id VARCHAR(64) NOT NULL,
    acquired_at TIMESTAMP NOT NULL,
    released_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_batch_item_locks_batch_id ON batch_item_locks(batch_id);
CREATE INDEX IF NOT EXISTS ix_batch_item_locks_item_id ON batch_item_locks(item_id);
CREATE INDEX IF NOT EXISTS ix_batch_item_locks_released_at ON batch_item_locks(released_at);

CREATE TABLE IF NOT EXISTS batch_audit_log (
    id VARCHAR(36) PRIMARY KEY,
    batch_id VARCHAR(36) NOT NULL,
    operation VARCHAR(64) NOT NULL,
    item_id VARCHAR(64) NOT NULL,
    actor VARCHAR(64) NOT NULL DEFAULT 'system',
    status VARCHAR(16) NOT NULL,
    message TEXT,
    before_json TEXT,
    after_json TEXT,
    idempotency_key VARCHAR(64),
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_batch_audit_batch_id ON batch_audit_log(batch_id);
CREATE INDEX IF NOT EXISTS ix_batch_audit_operation ON batch_audit_log(operation);
CREATE INDEX IF NOT EXISTS ix_batch_audit_item_id ON batch_audit_log(item_id);
CREATE INDEX IF NOT EXISTS ix_batch_audit_idempotency_key ON batch_audit_log(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_batch_audit_created_at ON batch_audit_log(created_at);
