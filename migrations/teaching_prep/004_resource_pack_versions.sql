CREATE TABLE resource_pack_versions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    lesson_node_id TEXT NOT NULL,
    version_number INTEGER NOT NULL
        CHECK(version_number > 0),
    source_state_sha256 TEXT NOT NULL
        CHECK(length(source_state_sha256) = 64),
    pack_sha256 TEXT NOT NULL
        CHECK(length(pack_sha256) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(lesson_node_id, version_number),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_resource_pack_versions_lesson
ON resource_pack_versions(lesson_node_id, version_number DESC);

CREATE INDEX idx_resource_pack_versions_source_state
ON resource_pack_versions(lesson_node_id, source_state_sha256);
