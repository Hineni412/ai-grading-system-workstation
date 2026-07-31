ALTER TABLE knowledge_relations
ADD COLUMN prompt_version TEXT;

ALTER TABLE knowledge_relations
ADD COLUMN confidence REAL
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0));

ALTER TABLE knowledge_relations
ADD COLUMN conflict_codes_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS knowledge_relation_suggestion_runs (
    run_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'queued', 'running', 'completed', 'partial',
            'failed', 'cancelled'
        )),
    prompt_version TEXT NOT NULL,
    cancellation_requested INTEGER NOT NULL DEFAULT 0
        CHECK (cancellation_requested IN (0, 1)),
    request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    last_error_category TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(run_id) <> ''),
    CHECK (TRIM(operation_id) <> ''),
    CHECK (TRIM(operation_fingerprint) <> ''),
    CHECK (TRIM(prompt_version) <> '')
);

CREATE TABLE IF NOT EXISTS knowledge_relation_suggestion_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    allowed_types_json TEXT NOT NULL,
    evidence_summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'running', 'suggested', 'not_suggested',
            'failed', 'cancelled'
        )),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    outcome_code TEXT,
    relation_id TEXT,
    error_category TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (source_key <> target_key),
    CHECK (TRIM(candidate_id) <> ''),
    UNIQUE(run_id, candidate_id),
    FOREIGN KEY(run_id)
        REFERENCES knowledge_relation_suggestion_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY(source_key) REFERENCES knowledge_tag_identities(stable_key),
    FOREIGN KEY(target_key) REFERENCES knowledge_tag_identities(stable_key),
    FOREIGN KEY(relation_id) REFERENCES knowledge_relations(relation_id)
);

CREATE TABLE IF NOT EXISTS knowledge_relation_suggestion_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    batch_sequence INTEGER NOT NULL CHECK (batch_sequence >= 1),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    status TEXT NOT NULL
        CHECK (status IN ('started', 'succeeded', 'failed')),
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 1),
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    error_category TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    CHECK (TRIM(request_id) <> ''),
    CHECK (TRIM(model_name) <> ''),
    CHECK (TRIM(model_version) <> ''),
    CHECK (TRIM(prompt_version) <> ''),
    UNIQUE(run_id, batch_sequence, attempt),
    FOREIGN KEY(run_id)
        REFERENCES knowledge_relation_suggestion_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_relation_suggestion_runs_status
ON knowledge_relation_suggestion_runs(status, updated_at, run_id);

CREATE INDEX IF NOT EXISTS idx_relation_suggestion_items_pending
ON knowledge_relation_suggestion_items(run_id, status, id);

CREATE INDEX IF NOT EXISTS idx_relation_suggestion_requests_run
ON knowledge_relation_suggestion_requests(run_id, batch_sequence, attempt);
