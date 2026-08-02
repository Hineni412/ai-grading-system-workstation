CREATE TABLE IF NOT EXISTS support_ai_review_sessions (
    review_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    base_revision_number INTEGER NOT NULL CHECK (base_revision_number >= 1),
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'drafting', 'preview_ready', 'awaiting_teacher', 'proposal_ready',
            'applied', 'rejected', 'expired', 'invalidated', 'result_unknown'
        )
    ),
    active_turn INTEGER NOT NULL DEFAULT 1 CHECK (active_turn >= 1),
    model_operation_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(record_id) REFERENCES support_records(record_id) ON DELETE CASCADE,
    FOREIGN KEY(subject_id) REFERENCES student_subject_links(subject_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS support_ai_review_turns (
    turn_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL CHECK (turn_number >= 1),
    preview_id TEXT NOT NULL UNIQUE,
    preview_fingerprint TEXT NOT NULL,
    preview_object_id TEXT NOT NULL UNIQUE,
    teacher_supplement_object_id TEXT UNIQUE,
    model_operation_id TEXT UNIQUE,
    result_object_id TEXT UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN ('preview_ready', 'claimed', 'needs_information', 'proposal_ready', 'result_unknown', 'rejected')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(review_id, turn_number),
    FOREIGN KEY(review_id) REFERENCES support_ai_review_sessions(review_id) ON DELETE CASCADE,
    FOREIGN KEY(preview_object_id) REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT,
    FOREIGN KEY(teacher_supplement_object_id) REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT,
    FOREIGN KEY(result_object_id) REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT
);

ALTER TABLE student_card_entries ADD COLUMN source_record_id TEXT;
ALTER TABLE student_card_entries ADD COLUMN source_record_revision INTEGER;
ALTER TABLE student_card_entries ADD COLUMN review_id TEXT;
ALTER TABLE student_card_entries ADD COLUMN state TEXT NOT NULL DEFAULT 'active'
    CHECK (state IN ('active', 'superseded'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_student_card_active_record_revision
    ON student_card_entries(source_record_id, source_record_revision)
    WHERE source_record_id IS NOT NULL AND state = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS idx_student_card_review
    ON student_card_entries(review_id)
    WHERE review_id IS NOT NULL;
