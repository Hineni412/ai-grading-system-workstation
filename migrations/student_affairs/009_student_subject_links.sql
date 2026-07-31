CREATE TABLE IF NOT EXISTS student_subject_links (
    subject_id TEXT PRIMARY KEY,
    source_fingerprint BLOB NOT NULL UNIQUE,
    payload_object_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_student_subject_state
    ON student_subject_links(state, updated_at);
