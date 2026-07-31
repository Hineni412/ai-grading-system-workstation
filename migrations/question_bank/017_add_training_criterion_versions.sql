CREATE TABLE IF NOT EXISTS training_criterion_versions (
    version_id TEXT PRIMARY KEY,
    question_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    parent_version_id TEXT,
    source_content_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'training-criteria-draft-v1'),
    status TEXT NOT NULL
        CHECK (
            status IN (
                'proposed', 'approved', 'rejected', 'superseded', 'stale'
            )
        ),
    source_kind TEXT NOT NULL
        CHECK (
            source_kind IN (
                'combined_model',
                'confirmed_rubric_adapter',
                'teacher_manual',
                'backfill'
            )
        ),
    source_reference TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    criteria_hash TEXT NOT NULL,
    quality_status TEXT NOT NULL
        CHECK (quality_status IN ('passed', 'failed')),
    quality_codes_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    decision_by TEXT,
    decision_note TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(question_id, version_number),
    UNIQUE(question_id, source_kind, source_reference),
    CHECK (length(version_id) = 64),
    CHECK (length(source_content_hash) = 64),
    CHECK (length(criteria_hash) = 64),
    CHECK (TRIM(source_reference) <> ''),
    CHECK (json_valid(criteria_json)),
    CHECK (json_valid(quality_codes_json)),
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(parent_version_id)
        REFERENCES training_criterion_versions(version_id)
);

CREATE TABLE IF NOT EXISTS training_criterion_heads (
    question_id INTEGER PRIMARY KEY,
    current_version_id TEXT NOT NULL,
    approved_version_id TEXT,
    current_source_hash TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(current_source_hash) = 64),
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(current_version_id)
        REFERENCES training_criterion_versions(version_id),
    FOREIGN KEY(approved_version_id)
        REFERENCES training_criterion_versions(version_id)
);

CREATE TABLE IF NOT EXISTS training_criterion_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    version_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (
            event_type IN (
                'proposed',
                'edited',
                'approved',
                'rejected',
                'superseded',
                'stale'
            )
        ),
    actor_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(version_id)
        REFERENCES training_criterion_versions(version_id)
);

CREATE TABLE IF NOT EXISTS training_criterion_backfill_runs (
    run_id TEXT PRIMARY KEY,
    request_token TEXT NOT NULL UNIQUE,
    input_fingerprint TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'missing_only'
        CHECK (mode IN ('missing_only', 'regenerate')),
    question_ids_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'running',
                'partial',
                'succeeded',
                'failed',
                'cancelled'
            )
        ),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    CHECK (length(run_id) = 64),
    CHECK (length(request_token) = 32),
    CHECK (length(input_fingerprint) = 64),
    CHECK (json_valid(question_ids_json))
);

CREATE TABLE IF NOT EXISTS training_criterion_backfill_items (
    run_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'running',
                'succeeded',
                'failed',
                'cancelled',
                'skipped'
            )
        ),
    version_id TEXT,
    error_category TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(run_id, question_id),
    FOREIGN KEY(run_id)
        REFERENCES training_criterion_backfill_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(version_id)
        REFERENCES training_criterion_versions(version_id)
);

CREATE INDEX IF NOT EXISTS idx_training_criterion_versions_question
ON training_criterion_versions(question_id, version_number DESC);

CREATE INDEX IF NOT EXISTS idx_training_criterion_versions_status
ON training_criterion_versions(status, question_id);

CREATE INDEX IF NOT EXISTS idx_training_criterion_backfill_status
ON training_criterion_backfill_items(status, run_id, question_id);
