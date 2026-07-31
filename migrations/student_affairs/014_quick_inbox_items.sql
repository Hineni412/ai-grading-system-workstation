CREATE TABLE IF NOT EXISTS quick_inbox_items (
    inbox_item_id TEXT PRIMARY KEY,
    subject_id TEXT,
    payload_object_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN ('draft', 'confirmed', 'cancelled')
    ),
    target_kind TEXT CHECK (
        target_kind IS NULL OR target_kind IN ('support_record', 'action', 'sop')
    ),
    target_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_quick_inbox_state
    ON quick_inbox_items(state, created_at);
