CREATE TABLE IF NOT EXISTS action_dependencies (
    action_id TEXT NOT NULL,
    depends_on_action_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(action_id, depends_on_action_id),
    FOREIGN KEY(action_id) REFERENCES actions(action_id),
    FOREIGN KEY(depends_on_action_id) REFERENCES actions(action_id),
    CHECK(action_id <> depends_on_action_id)
);

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES actions(action_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_reminders_action ON reminders(action_id);
