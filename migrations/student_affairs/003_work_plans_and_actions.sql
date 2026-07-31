CREATE TABLE IF NOT EXISTS work_plans (
    plan_id TEXT PRIMARY KEY,
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES work_plans(plan_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_actions_plan ON actions(plan_id);

CREATE TABLE IF NOT EXISTS action_audit_events (
    event_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES actions(action_id)
);

CREATE INDEX IF NOT EXISTS idx_action_audit_action
    ON action_audit_events(action_id, created_at);
