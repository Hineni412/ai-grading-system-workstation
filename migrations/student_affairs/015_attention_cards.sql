CREATE TABLE IF NOT EXISTS attention_cards (
    attention_card_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    evidence_version_id TEXT NOT NULL UNIQUE,
    payload_object_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN ('draft', 'resolved', 'invalidated')
    ),
    decision TEXT CHECK (
        decision IS NULL OR decision IN (
            'follow_up', 'observe', 'no_action'
        )
    ),
    action_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_version_id)
        REFERENCES evidence_versions(evidence_version_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id),
    FOREIGN KEY(action_id) REFERENCES actions(action_id)
);

CREATE INDEX IF NOT EXISTS idx_attention_cards_subject
    ON attention_cards(subject_id, state, created_at);
