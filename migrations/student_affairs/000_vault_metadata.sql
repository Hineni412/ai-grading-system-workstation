CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    checksum TEXT,
    success INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vault_metadata (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    instance_id TEXT NOT NULL UNIQUE,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    kdf_n INTEGER NOT NULL,
    kdf_r INTEGER NOT NULL,
    kdf_p INTEGER NOT NULL,
    password_salt BLOB NOT NULL,
    password_nonce BLOB NOT NULL,
    wrapped_vmk_password BLOB NOT NULL,
    recovery_salt BLOB NOT NULL,
    recovery_nonce BLOB NOT NULL,
    wrapped_vmk_recovery BLOB NOT NULL,
    brk_vmk_nonce BLOB NOT NULL,
    wrapped_brk_vmk BLOB NOT NULL,
    brk_recovery_nonce BLOB NOT NULL,
    wrapped_brk_recovery BLOB NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    blocked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS encrypted_objects (
    object_id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    cek_nonce BLOB NOT NULL,
    wrapped_cek BLOB NOT NULL,
    payload_nonce BLOB NOT NULL,
    payload_ciphertext BLOB NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_encrypted_objects_type
    ON encrypted_objects(object_type);
