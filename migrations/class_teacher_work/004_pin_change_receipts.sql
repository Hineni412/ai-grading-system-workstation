CREATE TABLE IF NOT EXISTS protection_change_operations (
    operation_id TEXT PRIMARY KEY,
    change_kind TEXT NOT NULL CHECK (change_kind = 'pin_change'),
    request_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('staged', 'activated', 'completed')),
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

ALTER TABLE sensitive_protection_pending ADD COLUMN operation_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sensitive_protection_pending_operation
    ON sensitive_protection_pending(operation_id)
    WHERE operation_id IS NOT NULL;
