CREATE TABLE IF NOT EXISTS assessment_imports (
    import_id TEXT PRIMARY KEY,
    payload_object_id TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('existing_math', 'confirmed_spreadsheet')
    ),
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'superseded')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    occurred_on TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(import_id)
        REFERENCES assessment_imports(import_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_assessments_import
    ON assessments(import_id, occurred_on);
