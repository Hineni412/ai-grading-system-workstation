CREATE TABLE IF NOT EXISTS question_analysis_operations (
    operation_id TEXT PRIMARY KEY,
    input_fingerprint TEXT NOT NULL,
    contract_version TEXT NOT NULL
        CHECK (contract_version IN ('combined-v2', 'tag-only-v1')),
    requested_projection TEXT NOT NULL
        CHECK (
            requested_projection IN ('both', 'tag', 'training_criteria')
        ),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'partial', 'succeeded', 'failed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(operation_id) <> ''),
    CHECK (length(input_fingerprint) = 64)
);

CREATE TABLE IF NOT EXISTS question_analysis_items (
    operation_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    source_content_hash TEXT NOT NULL,
    tag_status TEXT NOT NULL
        CHECK (
            tag_status IN (
                'pending', 'succeeded', 'failed', 'cancelled', 'not_requested'
            )
        ),
    criteria_status TEXT NOT NULL
        CHECK (
            criteria_status IN (
                'pending', 'succeeded', 'failed', 'cancelled', 'not_requested'
            )
        ),
    tag_payload_hash TEXT,
    criteria_payload_json TEXT,
    tag_error_category TEXT NOT NULL DEFAULT '',
    criteria_error_category TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(operation_id, question_id),
    CHECK (length(source_content_hash) = 64),
    CHECK (
        tag_payload_hash IS NULL OR length(tag_payload_hash) = 64
    ),
    CHECK (
        criteria_payload_json IS NULL
        OR json_valid(criteria_payload_json)
    ),
    FOREIGN KEY(operation_id)
        REFERENCES question_analysis_operations(operation_id)
        ON DELETE CASCADE,
    FOREIGN KEY(question_id)
        REFERENCES questions(id)
);

CREATE TABLE IF NOT EXISTS question_analysis_requests (
    request_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    projection TEXT NOT NULL
        CHECK (projection IN ('both', 'tag', 'training_criteria')),
    batch_hash TEXT NOT NULL,
    question_ids_json TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'succeeded', 'failed')),
    model_name TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER NOT NULL DEFAULT 0
        CHECK (completion_tokens >= 0),
    total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    error_category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    CHECK (length(request_id) = 64),
    CHECK (length(batch_hash) = 64),
    CHECK (json_valid(question_ids_json)),
    FOREIGN KEY(operation_id)
        REFERENCES question_analysis_operations(operation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_question_analysis_items_status
ON question_analysis_items(tag_status, criteria_status);

CREATE INDEX IF NOT EXISTS idx_question_analysis_requests_operation
ON question_analysis_requests(operation_id, created_at, request_id);
