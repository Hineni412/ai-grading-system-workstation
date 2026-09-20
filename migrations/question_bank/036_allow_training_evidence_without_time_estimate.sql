-- migration-policy: rebuild-tables training_evidence_records
-- New recommendations do not estimate duration. Preserve legacy values and all
-- evidence identities; a missing duration has no effect on mastery.

CREATE TABLE training_evidence_records_optional_minutes (
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
            AND (expected_minutes IS NULL OR expected_minutes > 0)
        )
        OR status = 'withdrawn'
    ),
    UNIQUE(submission_id, submission_revision, task_item_code),
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE RESTRICT
);

INSERT INTO training_evidence_records_optional_minutes (
    evidence_id,
    submission_id,
    submission_revision,
    task_item_code,
    source_review_revision,
    status,
    student_id,
    stable_key,
    occurred_at,
    achieved_points,
    total_points,
    coverage_ratio,
    difficulty_weight,
    evidence_weight,
    expected_minutes,
    criterion_version_id,
    criterion_hash,
    payload_hash,
    final_points_json,
    teacher_corrections_json,
    source_json,
    created_at,
    updated_at
)
SELECT
    evidence_id,
    submission_id,
    submission_revision,
    task_item_code,
    source_review_revision,
    status,
    student_id,
    stable_key,
    occurred_at,
    achieved_points,
    total_points,
    coverage_ratio,
    difficulty_weight,
    evidence_weight,
    expected_minutes,
    criterion_version_id,
    criterion_hash,
    payload_hash,
    final_points_json,
    teacher_corrections_json,
    source_json,
    created_at,
    updated_at
FROM training_evidence_records;

DROP TABLE training_evidence_records;
ALTER TABLE training_evidence_records_optional_minutes RENAME TO training_evidence_records;

CREATE INDEX idx_training_evidence_student_key
ON training_evidence_records(student_id, stable_key, status, occurred_at);
CREATE INDEX idx_training_evidence_submission
ON training_evidence_records(submission_id, submission_revision, status);

