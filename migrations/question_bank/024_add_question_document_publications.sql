CREATE TABLE IF NOT EXISTS question_document_publications (
    operation_id TEXT PRIMARY KEY,
    source_revision TEXT NOT NULL,
    snapshot_revision TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('staging', 'published', 'recovery_required')),
    manifest_sha256 TEXT NOT NULL,
    paper_id INTEGER,
    question_ids_json TEXT NOT NULL DEFAULT '[]',
    receipt_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(operation_id) <> ''),
    CHECK (length(source_revision) = 64),
    CHECK (length(snapshot_revision) = 64),
    CHECK (length(manifest_sha256) = 64),
    CHECK (json_valid(question_ids_json)),
    CHECK (receipt_json IS NULL OR json_valid(receipt_json)),
    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS question_document_items (
    operation_id TEXT NOT NULL,
    source_question_id TEXT NOT NULL,
    content_revision TEXT NOT NULL,
    bank_question_id INTEGER,
    rich_content_path TEXT NOT NULL,
    asset_paths_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL
        CHECK (state IN ('staged', 'published', 'recovery_required')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(operation_id, source_question_id),
    CHECK (TRIM(source_question_id) <> ''),
    CHECK (length(content_revision) = 64),
    CHECK (TRIM(rich_content_path) <> ''),
    CHECK (json_valid(asset_paths_json)),
    FOREIGN KEY(operation_id)
        REFERENCES question_document_publications(operation_id) ON DELETE RESTRICT,
    FOREIGN KEY(bank_question_id) REFERENCES questions(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS question_content_revisions (
    question_id INTEGER PRIMARY KEY,
    content_revision TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_regions_json TEXT NOT NULL,
    rich_content_path TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'superseded')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(content_revision) = 64),
    CHECK (length(source_revision) = 64),
    CHECK (json_valid(source_regions_json)),
    CHECK (TRIM(rich_content_path) <> ''),
    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS question_document_publication_outbox (
    outbox_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'failed')),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    delivered_at TEXT,
    CHECK (length(outbox_id) = 64),
    CHECK (length(payload_sha256) = 64),
    CHECK (json_valid(payload_json)),
    UNIQUE(operation_id, payload_sha256),
    FOREIGN KEY(operation_id)
        REFERENCES question_document_publications(operation_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_question_document_publications_state
ON question_document_publications(state, updated_at, operation_id);

CREATE INDEX IF NOT EXISTS idx_question_document_items_bank_question
ON question_document_items(bank_question_id, state);

CREATE INDEX IF NOT EXISTS idx_question_document_outbox_status
ON question_document_publication_outbox(status, updated_at, outbox_id);
