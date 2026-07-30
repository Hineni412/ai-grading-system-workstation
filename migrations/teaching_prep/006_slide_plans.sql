CREATE TABLE slide_plan_versions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    lesson_draft_id TEXT NOT NULL,
    resource_pack_id TEXT NOT NULL,
    version_number INTEGER NOT NULL
        CHECK(version_number > 0),
    based_on_plan_id TEXT,
    source_ppt_state_sha256 TEXT NOT NULL
        CHECK(length(source_ppt_state_sha256) = 64),
    status TEXT NOT NULL
        CHECK(status IN ('in_review', 'approved')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(lesson_draft_id, version_number),
    FOREIGN KEY(lesson_draft_id)
        REFERENCES lesson_draft_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(resource_pack_id)
        REFERENCES resource_pack_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(based_on_plan_id)
        REFERENCES slide_plan_versions(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_slide_plan_versions_draft
ON slide_plan_versions(lesson_draft_id, version_number DESC);

CREATE INDEX idx_slide_plan_versions_pack
ON slide_plan_versions(resource_pack_id, created_at DESC);
