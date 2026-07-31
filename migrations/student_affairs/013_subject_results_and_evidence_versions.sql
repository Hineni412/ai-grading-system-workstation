CREATE TABLE IF NOT EXISTS subject_results (
    result_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    result_state TEXT NOT NULL CHECK (
        result_state IN (
            'normal', 'absent', 'exempt', 'missing',
            'incomplete', 'makeup'
        )
    ),
    created_at TEXT NOT NULL,
    FOREIGN KEY(assessment_id)
        REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_results_subject
    ON subject_results(subject_id, created_at);

CREATE TABLE IF NOT EXISTS rank_contexts (
    rank_context_id TEXT PRIMARY KEY,
    result_id TEXT NOT NULL UNIQUE,
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(result_id)
        REFERENCES subject_results(result_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE TABLE IF NOT EXISTS evidence_versions (
    evidence_version_id TEXT PRIMARY KEY,
    result_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    payload_object_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('active', 'superseded')),
    created_at TEXT NOT NULL,
    UNIQUE(result_id, version),
    FOREIGN KEY(result_id)
        REFERENCES subject_results(result_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE TABLE IF NOT EXISTS source_fingerprints (
    source_fingerprint BLOB PRIMARY KEY,
    import_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(import_id)
        REFERENCES assessment_imports(import_id) ON DELETE CASCADE
);
