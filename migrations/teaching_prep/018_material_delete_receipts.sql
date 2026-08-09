CREATE TABLE material_delete_receipts (
    operation_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL
        CHECK(length(source_id) = 32),
    source_display_name TEXT NOT NULL
        CHECK(length(trim(source_display_name)) BETWEEN 1 AND 120),
    expected_revision INTEGER NOT NULL
        CHECK(expected_revision > 0),
    preview_version TEXT NOT NULL
        CHECK(length(preview_version) = 64),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    impact_json TEXT NOT NULL,
    staging_manifest_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(operation_id)
        REFERENCES teaching_prep_operations(operation_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_material_delete_receipts_source
ON material_delete_receipts(source_id, created_at);
