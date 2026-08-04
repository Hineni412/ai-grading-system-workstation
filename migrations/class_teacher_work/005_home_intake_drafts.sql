CREATE TABLE IF NOT EXISTS ordinary_home_intake_drafts (
    draft_id TEXT PRIMARY KEY,
    root_operation_id TEXT NOT NULL UNIQUE,
    result_kind TEXT NOT NULL CHECK (result_kind = 'ordinary_plan'),
    current_version INTEGER NOT NULL CHECK (current_version >= 1),
    state TEXT NOT NULL CHECK (state IN ('open', 'adopting', 'adopted', 'discarded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    adopted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ordinary_home_intake_drafts_state_updated
    ON ordinary_home_intake_drafts(state, updated_at DESC);

CREATE TABLE IF NOT EXISTS ordinary_home_intake_draft_versions (
    draft_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    source_operation_id TEXT NOT NULL UNIQUE,
    parent_version INTEGER,
    content_fingerprint TEXT NOT NULL,
    operation_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(draft_id, version),
    FOREIGN KEY(draft_id) REFERENCES ordinary_home_intake_drafts(draft_id) ON DELETE CASCADE
);
