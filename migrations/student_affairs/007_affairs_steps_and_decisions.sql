CREATE TABLE IF NOT EXISTS affairs (
    affair_id TEXT PRIMARY KEY,
    template_version_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('active', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    FOREIGN KEY(template_version_id)
        REFERENCES sop_template_versions(template_version_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id),
    FOREIGN KEY(plan_id) REFERENCES work_plans(plan_id)
);

CREATE TABLE IF NOT EXISTS affair_occurrences (
    occurrence_id TEXT PRIMARY KEY,
    affair_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(affair_id, sequence),
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE TABLE IF NOT EXISTS step_instances (
    step_instance_id TEXT PRIMARY KEY,
    affair_id TEXT NOT NULL,
    occurrence_id TEXT NOT NULL,
    template_step_key TEXT NOT NULL,
    action_id TEXT UNIQUE,
    payload_object_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN (
            'blocked', 'ready', 'in_progress', 'waiting',
            'completed', 'waived', 'superseded'
        )
    ),
    is_required INTEGER NOT NULL CHECK (is_required IN (0, 1)),
    is_safety_required INTEGER NOT NULL CHECK (is_safety_required IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(occurrence_id, template_step_key),
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id),
    FOREIGN KEY(occurrence_id) REFERENCES affair_occurrences(occurrence_id),
    FOREIGN KEY(action_id) REFERENCES actions(action_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_steps_affair_state
    ON step_instances(affair_id, state);

CREATE TABLE IF NOT EXISTS decision_records (
    decision_id TEXT PRIMARY KEY,
    affair_id TEXT NOT NULL,
    step_instance_id TEXT,
    decision_kind TEXT NOT NULL CHECK (
        decision_kind IN ('teacher', 'school', 'ai_suggestion')
    ),
    payload_object_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id),
    FOREIGN KEY(step_instance_id) REFERENCES step_instances(step_instance_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id)
);
