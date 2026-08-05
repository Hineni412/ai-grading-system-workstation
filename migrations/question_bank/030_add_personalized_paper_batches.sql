CREATE TABLE IF NOT EXISTS personalized_paper_batches (
    batch_run_id TEXT PRIMARY KEY,
    operation_token TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    request_json TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK (draft_revision >= 1),
    paper_batch_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'creating'
        CHECK (status IN ('creating', 'complete', 'partial', 'failed', 'cancelled')),
    requested_count INTEGER NOT NULL DEFAULT 0 CHECK (requested_count >= 0),
    succeeded_count INTEGER NOT NULL DEFAULT 0 CHECK (succeeded_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    manifest_path TEXT,
    bundle_path TEXT,
    frozen_bundle_path TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(batch_run_id) = 64),
    CHECK (length(operation_token) = 32),
    CHECK (length(operation_fingerprint) = 64),
    CHECK (length(draft_id) = 64),
    CHECK (length(paper_batch_id) = 64),
    FOREIGN KEY(draft_id)
        REFERENCES personalized_recommendation_drafts(draft_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS personalized_paper_batch_items (
    batch_run_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    item_order INTEGER NOT NULL CHECK (item_order >= 1),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    paper_instance_id TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(batch_run_id, student_id),
    CHECK (
        (status IN ('pending', 'running') AND paper_instance_id IS NULL AND error_code IS NULL)
        OR (status = 'succeeded' AND paper_instance_id IS NOT NULL AND error_code IS NULL)
        OR (status = 'failed' AND paper_instance_id IS NULL AND error_code IS NOT NULL)
    ),
    FOREIGN KEY(batch_run_id)
        REFERENCES personalized_paper_batches(batch_run_id)
        ON DELETE CASCADE,
    FOREIGN KEY(paper_instance_id)
        REFERENCES personalized_paper_instances(paper_instance_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_personalized_paper_batches_draft
ON personalized_paper_batches(draft_id, created_at DESC, batch_run_id);

CREATE INDEX IF NOT EXISTS idx_personalized_paper_batch_items_instance
ON personalized_paper_batch_items(paper_instance_id);
