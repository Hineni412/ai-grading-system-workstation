CREATE TABLE IF NOT EXISTS affair_participants (
    participant_id TEXT PRIMARY KEY,
    affair_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_affair_participants_affair
    ON affair_participants(affair_id);

CREATE TABLE IF NOT EXISTS affair_events (
    event_id TEXT PRIMARY KEY,
    affair_id TEXT NOT NULL,
    step_instance_id TEXT,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id),
    FOREIGN KEY(step_instance_id) REFERENCES step_instances(step_instance_id)
);

CREATE INDEX IF NOT EXISTS idx_affair_events_affair
    ON affair_events(affair_id, created_at);
