CREATE TABLE IF NOT EXISTS handoff_adoption_receipts (
    adoption_id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL UNIQUE,
    handling_mode TEXT NOT NULL CHECK (handling_mode IN ('record', 'plan_calendar', 'sop')),
    formal_object_type TEXT NOT NULL,
    formal_object_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK (draft_revision >= 1),
    target_revision TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_handoff_adoption_receipts_object
    ON handoff_adoption_receipts(formal_object_type, formal_object_id);

CREATE TABLE IF NOT EXISTS class_teacher_affair_records (
    record_id TEXT PRIMARY KEY,
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
