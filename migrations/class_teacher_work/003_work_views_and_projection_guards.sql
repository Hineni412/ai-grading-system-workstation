CREATE TABLE IF NOT EXISTS work_node_events (
    event_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('status', 'reschedule', 'progress', 'collection')
    ),
    summary TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES work_nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_work_node_events_node
    ON work_node_events(node_id, created_at, event_id);

CREATE TABLE IF NOT EXISTS work_collection_snapshots (
    node_id TEXT PRIMARY KEY,
    expected_count INTEGER NOT NULL DEFAULT 0 CHECK (expected_count >= 0),
    received_count INTEGER NOT NULL DEFAULT 0 CHECK (received_count >= 0),
    needs_review_count INTEGER NOT NULL DEFAULT 0 CHECK (needs_review_count >= 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES work_nodes(node_id) ON DELETE CASCADE,
    CHECK(received_count <= expected_count),
    CHECK(needs_review_count <= received_count)
);

ALTER TABLE work_nodes ADD COLUMN projection_type TEXT CHECK (
    projection_type IS NULL OR projection_type IN (
        'sensitive_affair', 'attention_followup', 'student_support'
    )
);

ALTER TABLE work_nodes ADD COLUMN projection_fingerprint TEXT;

CREATE TABLE IF NOT EXISTS work_projection_receipts (
    projection_id TEXT PRIMARY KEY,
    source_revision INTEGER NOT NULL CHECK (source_revision >= 1),
    envelope_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'tombstoned')),
    updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS restricted_projection_update_guard
BEFORE UPDATE OF title, details, status, due_date ON work_nodes
WHEN OLD.classification = 'restricted_projection'
     AND NEW.revision <= OLD.revision
BEGIN
    SELECT RAISE(ABORT, 'restricted_projection_read_only');
END;
