CREATE TABLE IF NOT EXISTS paper_permanent_delete_receipts (
    request_token TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
