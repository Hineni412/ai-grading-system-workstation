CREATE TABLE teaching_prep_adaptation_trace_events (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    operation_id TEXT NOT NULL,
    lesson_node_id TEXT NOT NULL,
    seq INTEGER NOT NULL
        CHECK(seq > 0),
    event_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(operation_id, seq),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_adaptation_trace_lesson
ON teaching_prep_adaptation_trace_events(lesson_node_id, operation_id, seq);
