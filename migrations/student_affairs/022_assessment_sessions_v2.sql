CREATE TABLE IF NOT EXISTS assessment_sessions (
    session_id TEXT PRIMARY KEY,
    payload_object_id TEXT NOT NULL UNIQUE,
    source_fingerprint BLOB NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('active', 'superseded')),
    metadata_complete INTEGER NOT NULL CHECK (metadata_complete IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS assessment_session_members (
    session_id TEXT NOT NULL,
    evidence_version_id TEXT NOT NULL UNIQUE,
    measure_role TEXT NOT NULL CHECK (measure_role IN ('subject_score', 'total_score')),
    measure_key BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(session_id, evidence_version_id),
    FOREIGN KEY(session_id) REFERENCES assessment_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_version_id) REFERENCES evidence_versions(evidence_version_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_assessment_session_measure
    ON assessment_session_members(session_id, measure_role, measure_key);

CREATE TABLE IF NOT EXISTS attention_card_evidence_links (
    attention_card_id TEXT NOT NULL,
    evidence_version_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(attention_card_id, evidence_version_id),
    FOREIGN KEY(attention_card_id) REFERENCES attention_cards(attention_card_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_version_id) REFERENCES evidence_versions(evidence_version_id) ON DELETE CASCADE
);
