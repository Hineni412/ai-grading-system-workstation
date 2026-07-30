CREATE TABLE IF NOT EXISTS personalized_recommendation_drafts (
    draft_id TEXT PRIMARY KEY,
    request_token TEXT NOT NULL UNIQUE,
    input_fingerprint TEXT NOT NULL,
    result_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    source_version TEXT NOT NULL,
    request_json TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'reviewed')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(draft_id) = 64),
    CHECK (length(request_token) = 32),
    CHECK (length(input_fingerprint) = 64),
    CHECK (length(result_version) = 64),
    CHECK (length(source_version) = 64),
    CHECK (json_valid(request_json)),
    CHECK (json_valid(draft_json))
);

CREATE TABLE IF NOT EXISTS personalized_recommendation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL,
    request_token TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    action TEXT NOT NULL
        CHECK (action IN ('created', 'locked', 'unlocked', 'excluded', 'replaced')),
    actor_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    resulting_draft_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(request_token) = 32),
    CHECK (length(command_hash) = 64),
    CHECK (TRIM(actor_ref) <> ''),
    CHECK (TRIM(reason) <> ''),
    CHECK (json_valid(before_json)),
    CHECK (json_valid(after_json)),
    CHECK (json_valid(resulting_draft_json)),
    UNIQUE(draft_id, request_token),
    FOREIGN KEY(draft_id)
        REFERENCES personalized_recommendation_drafts(draft_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personalized_draft_status
ON personalized_recommendation_drafts(status, updated_at, draft_id);

CREATE INDEX IF NOT EXISTS idx_personalized_event_draft
ON personalized_recommendation_events(draft_id, resulting_revision);
