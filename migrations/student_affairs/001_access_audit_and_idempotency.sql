CREATE TABLE IF NOT EXISTS access_audit (
    audit_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    object_id TEXT,
    result TEXT NOT NULL,
    error_category TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_access_audit_created
    ON access_audit(created_at);

CREATE TABLE IF NOT EXISTS idempotency_ledger (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
