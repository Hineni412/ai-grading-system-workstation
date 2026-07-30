CREATE TABLE lesson_draft_versions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    resource_pack_id TEXT NOT NULL,
    version_number INTEGER NOT NULL
        CHECK(version_number > 0),
    based_on_draft_id TEXT,
    operation_id TEXT UNIQUE,
    source_kind TEXT NOT NULL
        CHECK(source_kind IN ('local_template', 'model', 'teacher')),
    model_label TEXT,
    status TEXT NOT NULL
        CHECK(status IN ('draft', 'confirmed')),
    payload_json TEXT NOT NULL,
    capacity_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(resource_pack_id, version_number),
    FOREIGN KEY(resource_pack_id)
        REFERENCES resource_pack_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(based_on_draft_id)
        REFERENCES lesson_draft_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(operation_id)
        REFERENCES teaching_prep_operations(operation_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_lesson_draft_versions_pack
ON lesson_draft_versions(resource_pack_id, version_number DESC);

CREATE INDEX idx_lesson_draft_versions_status
ON lesson_draft_versions(resource_pack_id, status, created_at DESC);
