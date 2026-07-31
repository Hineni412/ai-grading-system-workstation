CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    record_id TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(record_id) REFERENCES support_records(record_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS observation_evidence_links (
    observation_id TEXT NOT NULL,
    evidence_record_id TEXT NOT NULL,
    relation_kind TEXT NOT NULL CHECK (
        relation_kind IN ('supports', 'counterexample', 'context')
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY(observation_id, evidence_record_id, relation_kind),
    FOREIGN KEY(observation_id)
        REFERENCES observations(observation_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_record_id)
        REFERENCES support_records(record_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS support_plans (
    support_plan_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    action_id TEXT,
    payload_object_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN ('active', 'completed', 'cancelled', 'archived')
    ),
    review_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(action_id) REFERENCES actions(action_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE TABLE IF NOT EXISTS encrypted_summary_cache (
    subject_id TEXT PRIMARY KEY,
    payload_object_id TEXT NOT NULL UNIQUE,
    source_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(subject_id)
        REFERENCES student_subject_links(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);
