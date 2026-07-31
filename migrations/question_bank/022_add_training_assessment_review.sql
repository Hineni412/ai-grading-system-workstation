ALTER TABLE training_assessment_runs
ADD COLUMN review_revision INTEGER NOT NULL DEFAULT 1
    CHECK (review_revision >= 1);

ALTER TABLE training_assessment_runs
ADD COLUMN control_state TEXT NOT NULL DEFAULT 'active'
    CHECK (control_state IN ('active', 'paused', 'cancelled'));

CREATE TABLE IF NOT EXISTS training_assessment_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    operation_token TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed',
                          'cancelled')),
    request_count INTEGER NOT NULL DEFAULT 0
        CHECK (request_count IN (0, 1)),
    error_code TEXT,
    model_name TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER NOT NULL DEFAULT 0
        CHECK (completion_tokens >= 0),
    total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(attempt_id) = 64),
    CHECK (length(operation_token) = 32),
    CHECK (length(request_fingerprint) = 64),
    UNIQUE(run_id, attempt_number),
    UNIQUE(operation_token),
    FOREIGN KEY(run_id) REFERENCES training_assessment_runs(run_id)
        ON DELETE CASCADE
);

INSERT OR IGNORE INTO training_assessment_attempts (
    attempt_id, run_id, attempt_number, operation_token,
    request_fingerprint, status, request_count, error_code, model_name,
    prompt_tokens, completion_tokens, total_tokens, latency_ms,
    started_at, finished_at, created_at, updated_at
)
SELECT
    run_id, run_id, 1, substr(run_id, 1, 32), run_id,
    CASE status
        WHEN 'running' THEN 'running'
        WHEN 'cancelled' THEN 'cancelled'
        WHEN 'failed' THEN 'failed'
        ELSE 'succeeded'
    END,
    request_count, error_code, model_name, prompt_tokens,
    completion_tokens, total_tokens, latency_ms, started_at, finished_at,
    created_at, updated_at
FROM training_assessment_runs;

CREATE TABLE IF NOT EXISTS training_point_locks (
    lock_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    task_item_code TEXT NOT NULL,
    point_id TEXT NOT NULL,
    final_state TEXT NOT NULL
        CHECK (final_state IN ('met', 'not_met', 'uncertain', 'unreadable')),
    teacher_evidence TEXT NOT NULL CHECK (TRIM(teacher_evidence) <> ''),
    teacher_reason TEXT NOT NULL CHECK (TRIM(teacher_reason) <> ''),
    actor_ref TEXT NOT NULL CHECK (TRIM(actor_ref) <> ''),
    lock_revision INTEGER NOT NULL CHECK (lock_revision >= 1),
    expected_point_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(lock_id) = 64),
    CHECK (json_valid(expected_point_json)),
    UNIQUE(submission_id, submission_revision, task_item_code, point_id),
    FOREIGN KEY(run_id) REFERENCES training_assessment_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_assessment_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    operation_token TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('review_point', 'retry', 'pause', 'resume',
                              'cancel', 'recover')),
    actor_ref TEXT NOT NULL CHECK (TRIM(actor_ref) <> ''),
    from_review_revision INTEGER NOT NULL
        CHECK (from_review_revision >= 1),
    to_review_revision INTEGER NOT NULL
        CHECK (to_review_revision >= from_review_revision),
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(event_id) = 64),
    CHECK (length(operation_token) = 32),
    CHECK (length(request_fingerprint) = 64),
    CHECK (json_valid(detail_json)),
    UNIQUE(operation_token),
    FOREIGN KEY(run_id) REFERENCES training_assessment_runs(run_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_training_assessment_attempt_run
ON training_assessment_attempts(run_id, attempt_number);

CREATE INDEX IF NOT EXISTS idx_training_point_lock_run
ON training_point_locks(run_id, task_item_code, point_id);

CREATE INDEX IF NOT EXISTS idx_training_assessment_event_run
ON training_assessment_events(run_id, created_at);
