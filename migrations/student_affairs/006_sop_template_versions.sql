CREATE TABLE IF NOT EXISTS sop_template_versions (
    template_version_id TEXT PRIMARY KEY,
    template_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    definition_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(template_key, version),
    FOREIGN KEY(definition_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_sop_template_key
    ON sop_template_versions(template_key, version);
