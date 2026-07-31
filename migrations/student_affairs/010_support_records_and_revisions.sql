CREATE TABLE IF NOT EXISTS support_records (
    record_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    record_kind TEXT NOT NULL CHECK (
        record_kind IN (
            'fact', 'student_statement', 'reported_statement',
            'teacher_observation', 'provisional_judgment',
            'professional_conclusion', 'ai_draft'
        )
    ),
    state TEXT NOT NULL CHECK (
        state IN ('active', 'withdrawn', 'archived')
    ),
    current_revision INTEGER NOT NULL DEFAULT 1 CHECK (current_revision >= 1),
    observed_at TEXT NOT NULL,
    review_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_support_records_subject
    ON support_records(subject_id, state, expires_at);

CREATE TABLE IF NOT EXISTS record_revisions (
    revision_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(record_id, revision_number),
    FOREIGN KEY(record_id) REFERENCES support_records(record_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);
