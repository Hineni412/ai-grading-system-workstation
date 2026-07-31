CREATE TABLE semester_mapping_proposals (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    semester_id TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    source_state_sha256 TEXT NOT NULL
        CHECK(length(source_state_sha256) = 64),
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed', 'applied', 'rejected')),
    payload_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    applied_at TEXT,
    FOREIGN KEY(semester_id)
        REFERENCES teaching_semesters(id)
        ON DELETE CASCADE,
    FOREIGN KEY(operation_id)
        REFERENCES teaching_prep_operations(operation_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_semester_mapping_proposals
ON semester_mapping_proposals(semester_id, created_at DESC);
