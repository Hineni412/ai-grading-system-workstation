-- migration-policy: rebuild-tables question_solution_evidence_versions
-- Preserve existing evidence while widening the accepted contract to v2.

CREATE TABLE question_solution_evidence_versions_v2 (
    evidence_version_id TEXT PRIMARY KEY,
    question_id INTEGER NOT NULL,
    source_content_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK (
            schema_version IN (
                'question-solution-evidence-v1',
                'question-solution-evidence-v2'
            )
        ),
    content_hash TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded', 'stale')),
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('combined_model', 'teacher_manual', 'import', 'backfill')),
    source_reference TEXT NOT NULL,
    created_by TEXT NOT NULL,
    decision_by TEXT,
    decision_note TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    graph_release_id TEXT REFERENCES knowledge_graph_releases(release_id),
    CHECK (length(evidence_version_id) = 64),
    CHECK (length(source_content_hash) = 64),
    CHECK (length(content_hash) = 64),
    CHECK (json_valid(evidence_json)),
    CHECK (TRIM(source_reference) <> ''),
    CHECK (TRIM(created_by) <> ''),
    UNIQUE(question_id, source_kind, source_reference),
    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE RESTRICT
);

INSERT INTO question_solution_evidence_versions_v2 (
    evidence_version_id,
    question_id,
    source_content_hash,
    schema_version,
    content_hash,
    evidence_json,
    status,
    source_kind,
    source_reference,
    created_by,
    decision_by,
    decision_note,
    decided_at,
    created_at,
    updated_at,
    graph_release_id
)
SELECT
    evidence_version_id,
    question_id,
    source_content_hash,
    schema_version,
    content_hash,
    evidence_json,
    status,
    source_kind,
    source_reference,
    created_by,
    decision_by,
    decision_note,
    decided_at,
    created_at,
    updated_at,
    graph_release_id
FROM question_solution_evidence_versions;

DROP TABLE question_solution_evidence_versions;

ALTER TABLE question_solution_evidence_versions_v2
RENAME TO question_solution_evidence_versions;

CREATE INDEX idx_solution_evidence_question
ON question_solution_evidence_versions(
    question_id, status, source_content_hash, created_at
);
