CREATE TABLE IF NOT EXISTS student_card_entries (
    entry_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    model_operation_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id)
        REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_student_card_entries_subject
    ON student_card_entries(subject_id, created_at);

CREATE TABLE IF NOT EXISTS student_card_projection_outbox (
    event_id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    projection_id TEXT NOT NULL UNIQUE,
    projection_kind TEXT NOT NULL CHECK (
        projection_kind IN ('task', 'sop')
    ),
    due_date TEXT CHECK (
        due_date IS NULL OR (
            length(due_date) = 10 AND date(due_date) = due_date
        )
    ),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending', 'applied')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(entry_id)
        REFERENCES student_card_entries(entry_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_student_card_projection_pending
    ON student_card_projection_outbox(state, created_at);

CREATE TABLE IF NOT EXISTS student_model_artifacts (
    preview_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    preview_payload_object_id TEXT NOT NULL UNIQUE,
    result_payload_object_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(preview_payload_object_id)
        REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT,
    FOREIGN KEY(result_payload_object_id)
        REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_student_model_artifacts_subject
    ON student_model_artifacts(subject_id, created_at);
