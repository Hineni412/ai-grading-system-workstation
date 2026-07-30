CREATE TABLE IF NOT EXISTS mastery_v2_parameter_versions (
    parameter_version TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(parameter_version) = 64),
    CHECK (json_valid(parameters_json))
);

CREATE TABLE IF NOT EXISTS mastery_v2_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    parameter_version TEXT NOT NULL,
    as_of TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count >= 1),
    required_review_count INTEGER NOT NULL
        CHECK (required_review_count >= 1),
    max_absolute_delta REAL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(evaluation_id) = 64),
    FOREIGN KEY(parameter_version)
        REFERENCES mastery_v2_parameter_versions(parameter_version)
);

CREATE TABLE IF NOT EXISTS mastery_v2_evaluation_items (
    evaluation_id TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    requires_review INTEGER NOT NULL CHECK (requires_review IN (0, 1)),
    PRIMARY KEY(evaluation_id, item_hash),
    CHECK (length(item_hash) = 64),
    FOREIGN KEY(evaluation_id)
        REFERENCES mastery_v2_evaluations(evaluation_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mastery_v2_spot_checks (
    evaluation_id TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
    teacher_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(evaluation_id, item_hash),
    CHECK (TRIM(teacher_ref) <> ''),
    CHECK (TRIM(reason) <> ''),
    FOREIGN KEY(evaluation_id, item_hash)
        REFERENCES mastery_v2_evaluation_items(evaluation_id, item_hash)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mastery_v2_rollout_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    active_parameter_version TEXT,
    approved_evaluation_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_by TEXT,
    reason TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (
        (enabled = 0)
        OR (
            active_parameter_version IS NOT NULL
            AND approved_evaluation_id IS NOT NULL
            AND TRIM(COALESCE(updated_by, '')) <> ''
            AND TRIM(COALESCE(reason, '')) <> ''
        )
    ),
    FOREIGN KEY(active_parameter_version)
        REFERENCES mastery_v2_parameter_versions(parameter_version),
    FOREIGN KEY(approved_evaluation_id)
        REFERENCES mastery_v2_evaluations(evaluation_id)
);

INSERT OR IGNORE INTO mastery_v2_rollout_state (
    singleton_id,
    enabled,
    revision
) VALUES (1, 0, 1);

CREATE TABLE IF NOT EXISTS mastery_v2_rollout_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    parameter_version TEXT,
    evaluation_id TEXT,
    actor_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 1),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 2),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(actor_ref) <> ''),
    CHECK (TRIM(reason) <> ''),
    UNIQUE(resulting_revision)
);

CREATE INDEX IF NOT EXISTS idx_mastery_v2_evaluation_items_review
ON mastery_v2_evaluation_items(evaluation_id, requires_review);

CREATE INDEX IF NOT EXISTS idx_mastery_v2_spot_checks_decision
ON mastery_v2_spot_checks(evaluation_id, decision);
