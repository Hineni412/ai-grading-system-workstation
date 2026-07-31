CREATE TABLE IF NOT EXISTS encrypted_backup_records (
    backup_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL UNIQUE,
    source_instance_id TEXT NOT NULL,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('created', 'verified', 'restore_previewed', 'restored', 'invalid')
    ),
    created_at TEXT NOT NULL,
    verified_at TEXT
);
