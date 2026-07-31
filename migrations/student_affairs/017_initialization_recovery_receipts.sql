CREATE TABLE IF NOT EXISTS initialization_recovery_receipts (
    operation_id TEXT PRIMARY KEY,
    recovery_nonce BLOB NOT NULL,
    recovery_ciphertext BLOB NOT NULL,
    created_at TEXT NOT NULL
);
