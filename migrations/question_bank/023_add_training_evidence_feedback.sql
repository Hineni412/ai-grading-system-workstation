CREATE TABLE IF NOT EXISTS training_evidence_outbox (
    outbox_id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    task_item_code TEXT NOT NULL,
    source_review_revision INTEGER NOT NULL
        CHECK (source_review_revision >= 1),
    action TEXT NOT NULL CHECK (action IN ('publish', 'withdraw')),
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivering', 'delivered', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_code TEXT,
    actor_ref TEXT NOT NULL CHECK (TRIM(actor_ref) <> ''),
    reason TEXT NOT NULL CHECK (TRIM(reason) <> ''),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    delivered_at TEXT,
    CHECK (length(outbox_id) = 64),
    CHECK (length(evidence_id) = 64),
    CHECK (length(payload_hash) = 64),
    CHECK (json_valid(payload_json)),
    UNIQUE(evidence_id, source_review_revision, action),
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_evidence_records (
    evidence_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    task_item_code TEXT NOT NULL,
    source_review_revision INTEGER NOT NULL
        CHECK (source_review_revision >= 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'withdrawn')),
    student_id TEXT NOT NULL CHECK (TRIM(student_id) <> ''),
    stable_key TEXT NOT NULL CHECK (TRIM(stable_key) <> ''),
    occurred_at TEXT,
    achieved_points INTEGER,
    total_points INTEGER,
    coverage_ratio REAL,
    difficulty_weight REAL,
    evidence_weight REAL,
    expected_minutes INTEGER,
    criterion_version_id TEXT NOT NULL,
    criterion_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    final_points_json TEXT NOT NULL,
    teacher_corrections_json TEXT NOT NULL,
    source_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(evidence_id) = 64),
    CHECK (length(criterion_version_id) = 64),
    CHECK (length(criterion_hash) = 64),
    CHECK (length(payload_hash) = 64),
    CHECK (json_valid(final_points_json)),
    CHECK (json_valid(teacher_corrections_json)),
    CHECK (json_valid(source_json)),
    CHECK (
        (
            status = 'active'
            AND occurred_at IS NOT NULL
            AND achieved_points IS NOT NULL
            AND total_points IS NOT NULL
            AND total_points > 0
            AND achieved_points >= 0
            AND achieved_points <= total_points
            AND coverage_ratio IS NOT NULL
            AND coverage_ratio >= 0.0
            AND coverage_ratio <= 1.0
            AND difficulty_weight IS NOT NULL
            AND difficulty_weight > 0.0
            AND evidence_weight IS NOT NULL
            AND evidence_weight > 0.0
            AND expected_minutes IS NOT NULL
            AND expected_minutes > 0
        )
        OR status = 'withdrawn'
    ),
    UNIQUE(submission_id, submission_revision, task_item_code),
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_evidence_sync_events (
    event_id TEXT PRIMARY KEY,
    operation_token TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    source_review_revision INTEGER NOT NULL
        CHECK (source_review_revision >= 1),
    action TEXT NOT NULL CHECK (action IN ('publish', 'withdraw')),
    actor_ref TEXT NOT NULL CHECK (TRIM(actor_ref) <> ''),
    reason TEXT NOT NULL CHECK (TRIM(reason) <> ''),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(event_id) = 64),
    CHECK (length(operation_token) = 32),
    CHECK (length(operation_fingerprint) = 64),
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_feedback_snapshots (
    feedback_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    source_review_revision INTEGER NOT NULL
        CHECK (source_review_revision >= 1),
    evidence_version TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('publication_pending', 'partial', 'complete',
                          'withdrawn')),
    feedback_json TEXT NOT NULL,
    next_draft_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(feedback_id) = 64),
    CHECK (length(evidence_version) = 64),
    CHECK (next_draft_id IS NULL OR length(next_draft_id) = 64),
    CHECK (json_valid(feedback_json)),
    UNIQUE(submission_id, submission_revision),
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(next_draft_id)
        REFERENCES personalized_recommendation_drafts(draft_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_training_evidence_outbox_status
ON training_evidence_outbox(status, updated_at, outbox_id);

CREATE INDEX IF NOT EXISTS idx_training_evidence_student_key
ON training_evidence_records(student_id, stable_key, status, occurred_at);

CREATE INDEX IF NOT EXISTS idx_training_evidence_submission
ON training_evidence_records(submission_id, submission_revision, status);

CREATE INDEX IF NOT EXISTS idx_training_feedback_submission
ON training_feedback_snapshots(submission_id, submission_revision);
