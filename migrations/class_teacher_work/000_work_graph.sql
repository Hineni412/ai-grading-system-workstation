CREATE TABLE IF NOT EXISTS work_nodes (
    node_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'goal', 'task', 'waiting', 'decision', 'collection',
            'communication', 'sop', 'restricted_projection'
        )
    ),
    classification TEXT NOT NULL CHECK (
        classification IN ('ordinary', 'restricted_projection')
    ),
    title TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'draft', 'pending', 'in_progress', 'waiting',
            'completed', 'cancelled'
        )
    ),
    due_date TEXT CHECK (
        due_date IS NULL OR (
            length(due_date) = 10 AND date(due_date) = due_date
        )
    ),
    source_projection_id TEXT UNIQUE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_nodes_due
    ON work_nodes(due_date, status);

CREATE INDEX IF NOT EXISTS idx_work_nodes_classification
    ON work_nodes(classification, due_date);

CREATE TABLE IF NOT EXISTS work_edges (
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (
        relation IN ('contains', 'depends_on', 'next', 'review_of')
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_node_id, target_node_id, relation),
    FOREIGN KEY(source_node_id) REFERENCES work_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(target_node_id) REFERENCES work_nodes(node_id) ON DELETE CASCADE,
    CHECK(source_node_id <> target_node_id)
);

CREATE TABLE IF NOT EXISTS work_operations (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
