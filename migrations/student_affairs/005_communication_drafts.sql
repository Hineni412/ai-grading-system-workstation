CREATE TABLE IF NOT EXISTS communication_drafts (
    draft_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES actions(action_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_communication_drafts_action
    ON communication_drafts(action_id);
