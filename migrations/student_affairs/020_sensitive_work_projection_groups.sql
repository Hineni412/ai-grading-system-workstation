CREATE TABLE IF NOT EXISTS sensitive_work_groups (
    group_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('sensitive_affair', 'attention_followup', 'student_support')
    ),
    source_id TEXT NOT NULL,
    occurrence_id TEXT,
    projection_id TEXT NOT NULL UNIQUE,
    group_revision INTEGER NOT NULL DEFAULT 1 CHECK (group_revision >= 1),
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'in_progress', 'waiting', 'completed', 'cancelled')
    ),
    due_date TEXT CHECK (
        due_date IS NULL OR (length(due_date) = 10 AND date(due_date) = due_date)
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_kind, source_id, occurrence_id)
);

CREATE TABLE IF NOT EXISTS sensitive_work_projection_outbox (
    event_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK (source_revision >= 1),
    envelope_fingerprint TEXT NOT NULL,
    envelope_object_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending', 'applied')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(group_id, source_revision),
    FOREIGN KEY(group_id) REFERENCES sensitive_work_groups(group_id) ON DELETE CASCADE,
    FOREIGN KEY(envelope_object_id) REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_sensitive_projection_pending
    ON sensitive_work_projection_outbox(state, created_at, event_id);

CREATE TABLE IF NOT EXISTS sop_step_drafts (
    draft_id TEXT PRIMARY KEY,
    affair_id TEXT NOT NULL,
    occurrence_id TEXT NOT NULL,
    step_instance_id TEXT NOT NULL,
    draft_kind TEXT NOT NULL CHECK (draft_kind IN ('fact', 'communication')),
    payload_object_id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(step_instance_id, draft_kind),
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id) ON DELETE CASCADE,
    FOREIGN KEY(occurrence_id) REFERENCES affair_occurrences(occurrence_id) ON DELETE CASCADE,
    FOREIGN KEY(step_instance_id) REFERENCES step_instances(step_instance_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT
);
