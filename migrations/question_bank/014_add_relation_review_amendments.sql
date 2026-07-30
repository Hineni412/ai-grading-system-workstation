CREATE TABLE IF NOT EXISTS knowledge_relation_amendment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_id TEXT NOT NULL,
    from_source_key TEXT NOT NULL,
    from_target_key TEXT NOT NULL,
    from_relation_type TEXT NOT NULL
        CHECK (from_relation_type IN ('parent', 'prerequisite', 'related')),
    to_source_key TEXT NOT NULL,
    to_target_key TEXT NOT NULL,
    to_relation_type TEXT NOT NULL
        CHECK (to_relation_type IN ('parent', 'prerequisite', 'related')),
    actor_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 1),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 2),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(actor_ref) <> ''),
    CHECK (TRIM(reason) <> ''),
    UNIQUE(relation_id, resulting_revision),
    FOREIGN KEY(relation_id) REFERENCES knowledge_relations(relation_id)
);

CREATE INDEX IF NOT EXISTS idx_relation_amendments_timeline
ON knowledge_relation_amendment_events(relation_id, resulting_revision);
