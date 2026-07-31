CREATE TABLE IF NOT EXISTS training_assessment_runs (
    run_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    status TEXT NOT NULL
        CHECK (status IN ('running', 'succeeded', 'manual_review',
                          'failed', 'cancelled')),
    request_count INTEGER NOT NULL DEFAULT 0
        CHECK (request_count IN (0, 1)),
    expected_question_count INTEGER NOT NULL
        CHECK (expected_question_count >= 1),
    expected_point_count INTEGER NOT NULL
        CHECK (expected_point_count >= 1),
    model_name TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER NOT NULL DEFAULT 0
        CHECK (completion_tokens >= 0),
    total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    issue_codes_json TEXT NOT NULL DEFAULT '[]',
    error_code TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(run_id) = 64),
    CHECK (json_valid(issue_codes_json)),
    UNIQUE(submission_id, submission_revision),
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_question_results (
    question_result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    task_item_code TEXT NOT NULL,
    item_order INTEGER NOT NULL CHECK (item_order >= 1),
    criterion_version_id TEXT NOT NULL,
    criterion_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'manual_review')),
    met_count INTEGER NOT NULL DEFAULT 0 CHECK (met_count >= 0),
    not_met_count INTEGER NOT NULL DEFAULT 0 CHECK (not_met_count >= 0),
    uncertain_count INTEGER NOT NULL DEFAULT 0
        CHECK (uncertain_count >= 0),
    unreadable_count INTEGER NOT NULL DEFAULT 0
        CHECK (unreadable_count >= 0),
    total_count INTEGER NOT NULL CHECK (total_count >= 1),
    issue_codes_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(question_result_id) = 64),
    CHECK (length(criterion_version_id) = 64),
    CHECK (length(criterion_hash) = 64),
    CHECK (json_valid(issue_codes_json)),
    CHECK (
        status = 'manual_review'
        OR met_count + not_met_count + uncertain_count + unreadable_count
           = total_count
    ),
    UNIQUE(run_id, task_item_code),
    UNIQUE(submission_id, submission_revision, task_item_code),
    FOREIGN KEY(run_id) REFERENCES training_assessment_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_point_results (
    point_result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    question_result_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    submission_revision INTEGER NOT NULL CHECK (submission_revision >= 1),
    task_item_code TEXT NOT NULL,
    point_id TEXT NOT NULL,
    criterion_version_id TEXT NOT NULL,
    criterion_hash TEXT NOT NULL,
    expected_point_json TEXT NOT NULL,
    candidate_state TEXT NOT NULL
        CHECK (candidate_state IN ('met', 'not_met', 'uncertain',
                                   'unreadable')),
    model_evidence TEXT NOT NULL CHECK (TRIM(model_evidence) <> ''),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(point_result_id) = 64),
    CHECK (length(criterion_version_id) = 64),
    CHECK (length(criterion_hash) = 64),
    CHECK (json_valid(expected_point_json)),
    UNIQUE(run_id, task_item_code, point_id),
    UNIQUE(submission_id, submission_revision, task_item_code, point_id),
    FOREIGN KEY(run_id) REFERENCES training_assessment_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY(question_result_id)
        REFERENCES training_question_results(question_result_id)
        ON DELETE CASCADE,
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_training_assessment_submission
ON training_assessment_runs(submission_id, submission_revision, status);

CREATE INDEX IF NOT EXISTS idx_training_question_result_run
ON training_question_results(run_id, status, item_order);

CREATE INDEX IF NOT EXISTS idx_training_point_result_question
ON training_point_results(question_result_id, point_id);
