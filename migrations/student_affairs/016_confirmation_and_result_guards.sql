CREATE TABLE IF NOT EXISTS confirmation_claims (
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(entity_kind, entity_id),
    UNIQUE(operation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_subject_results_assessment_subject
    ON subject_results(assessment_id, subject_id);
