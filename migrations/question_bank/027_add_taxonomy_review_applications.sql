CREATE TABLE IF NOT EXISTS taxonomy_review_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    question_tag_id INTEGER,
    tag_type TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    preexisting INTEGER NOT NULL DEFAULT 0 CHECK (preexisting IN (0, 1)),
    before_revision TEXT,
    after_revision TEXT,
    write_status TEXT NOT NULL,
    undo_status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(operation_id, proposal_id, question_id, tag_type, tag_value),
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(question_tag_id) REFERENCES question_tags(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_taxonomy_review_applications_operation
ON taxonomy_review_applications(operation_id, write_status, undo_status);
