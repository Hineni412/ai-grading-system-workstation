CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (
        status IN ('queued','running','paused','succeeded','failed','cancelled')
    ),
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'queued',
    detail TEXT NOT NULL DEFAULT '',
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    started_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created
ON jobs(status, created_at, id);

CREATE INDEX IF NOT EXISTS idx_jobs_type_created
ON jobs(job_type, created_at, id);
