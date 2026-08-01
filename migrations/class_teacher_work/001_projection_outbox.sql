CREATE TABLE IF NOT EXISTS work_projection_outbox (
    event_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    projection_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'applied')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_projection_outbox_state
    ON work_projection_outbox(state, created_at);
