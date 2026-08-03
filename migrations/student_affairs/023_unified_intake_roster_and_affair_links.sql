CREATE TABLE IF NOT EXISTS class_roster_memberships (
    source_student_key TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL UNIQUE,
    source_revision TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'historical')),
    activated_at TEXT NOT NULL,
    historical_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id) REFERENCES student_subject_links(subject_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_class_roster_memberships_state
    ON class_roster_memberships(state, updated_at);

CREATE TABLE IF NOT EXISTS affair_student_links (
    affair_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(affair_id, subject_id),
    UNIQUE(participant_id),
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id) ON DELETE CASCADE,
    FOREIGN KEY(subject_id) REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(participant_id) REFERENCES affair_participants(participant_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_affair_student_links_subject
    ON affair_student_links(subject_id, created_at);
