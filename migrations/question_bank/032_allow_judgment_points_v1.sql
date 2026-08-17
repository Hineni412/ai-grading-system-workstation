-- migration-policy: rebuild-tables training_criterion_versions
-- Preserve existing criterion versions while accepting judgment-points-v1.

CREATE TABLE training_criterion_versions_jp_v1 (
    version_id TEXT PRIMARY KEY,
    question_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    parent_version_id TEXT,
    source_content_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK (
            schema_version IN (
                'training-criteria-draft-v1',
                'judgment-points-v1'
            )
        ),
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
    FOREIGN KEY(question_id) REFERENCES questions(id)
);

INSERT INTO training_criterion_versions_jp_v1 (
    version_id,
    question_id,
    version_number,
    parent_version_id,
    source_content_hash,
    schema_version,
    status,
    source_kind,
    source_reference,
    criteria_json,
    criteria_hash,
    quality_status,
    quality_codes_json,
    created_by,
    decision_by,
    decision_note,
    decided_at,
    created_at,
    updated_at
)
SELECT
    version_id,
    question_id,
    version_number,
    parent_version_id,
    source_content_hash,
    schema_version,
    status,
    source_kind,
    source_reference,
    criteria_json,
    criteria_hash,
    quality_status,
    quality_codes_json,
    created_by,
    decision_by,
    decision_note,
    decided_at,
    created_at,
    updated_at
FROM training_criterion_versions;

DROP TABLE training_criterion_versions;

ALTER TABLE training_criterion_versions_jp_v1
RENAME TO training_criterion_versions;

CREATE INDEX idx_training_criterion_versions_question
ON training_criterion_versions(question_id, version_number DESC);

CREATE INDEX idx_training_criterion_versions_status
ON training_criterion_versions(status, question_id);
