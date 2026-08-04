CREATE TABLE IF NOT EXISTS home_intake_drafts (
    draft_id TEXT PRIMARY KEY,
    root_operation_id TEXT NOT NULL UNIQUE,
    route TEXT NOT NULL CHECK (route IN ('ordinary', 'sensitive', 'emergency')),
    result_kind TEXT NOT NULL,
    current_version INTEGER NOT NULL CHECK (current_version >= 1),
    state TEXT NOT NULL CHECK (state IN ('open', 'adopting', 'adopted', 'discarded')),
    adopted_affair_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    adopted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_home_intake_drafts_state_updated
    ON home_intake_drafts(state, updated_at DESC);

CREATE TABLE IF NOT EXISTS home_intake_draft_versions (
    draft_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    payload_object_id TEXT NOT NULL UNIQUE,
    source_operation_id TEXT NOT NULL UNIQUE,
    parent_version INTEGER,
    content_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(draft_id, version),
    FOREIGN KEY(draft_id) REFERENCES home_intake_drafts(draft_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_home_intake_draft_versions_source
    ON home_intake_draft_versions(source_operation_id);
