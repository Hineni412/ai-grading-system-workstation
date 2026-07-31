CREATE TABLE teaching_prep_operations (
    operation_id TEXT PRIMARY KEY
        CHECK(length(operation_id) BETWEEN 8 AND 96),
    operation_type TEXT NOT NULL
        CHECK(length(trim(operation_type)) BETWEEN 1 AND 64),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK(length(idempotency_key) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    target_kind TEXT,
    target_id TEXT,
    status TEXT NOT NULL
        CHECK(status IN (
            'pending',
            'running',
            'succeeded',
            'failed',
            'cancelled',
            'interrupted'
        )),
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    finished_at TEXT
);

CREATE INDEX idx_teaching_prep_operations_status
ON teaching_prep_operations(status, updated_at);

CREATE TABLE lesson_preparations (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    title TEXT NOT NULL
        CHECK(length(trim(title)) BETWEEN 1 AND 160),
    class_name TEXT,
    state TEXT NOT NULL
        CHECK(state IN (
            'selecting_sources',
            'sources_locked',
            'evidence_ready',
            'draft_ready',
            'plan_in_review',
            'plan_approved',
            'executing',
            'verified',
            'published',
            'archived',
            'analysis_failed',
            'execution_failed',
            'verification_failed',
            'cancelled'
        )),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

CREATE INDEX idx_lesson_preparations_state
ON lesson_preparations(state, updated_at);
