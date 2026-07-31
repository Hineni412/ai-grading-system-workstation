CREATE TABLE IF NOT EXISTS personalized_paper_instances (
    paper_instance_id TEXT PRIMARY KEY,
    operation_token TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK (draft_revision >= 1),
    draft_result_version TEXT NOT NULL,
    paper_batch_id TEXT NOT NULL,
    series_version INTEGER NOT NULL CHECK (series_version >= 1),
    student_id TEXT NOT NULL,
    student_code_snapshot TEXT,
    student_name_snapshot TEXT,
    class_id_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'creating'
        CHECK (status IN ('creating', 'review_pending', 'failed', 'frozen')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    layout_version TEXT NOT NULL,
    budget_version TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    signing_secret TEXT NOT NULL,
    review_docx_path TEXT,
    review_docx_sha256 TEXT,
    reviewed_docx_path TEXT,
    reviewed_docx_sha256 TEXT,
    frozen_pdf_path TEXT,
    frozen_pdf_sha256 TEXT,
    page_count INTEGER,
    error_code TEXT,
    created_by TEXT NOT NULL,
    frozen_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    frozen_at TEXT,
    CHECK (length(paper_instance_id) = 64),
    CHECK (length(operation_token) = 32),
    CHECK (length(operation_fingerprint) = 64),
    CHECK (length(draft_id) = 64),
    CHECK (length(draft_result_version) = 64),
    CHECK (length(paper_batch_id) = 64),
    CHECK (length(signing_secret) = 64),
    CHECK (json_valid(budget_json)),
    CHECK (json_valid(snapshot_json)),
    CHECK (
        (status = 'frozen'
         AND reviewed_docx_path IS NOT NULL
         AND reviewed_docx_sha256 IS NOT NULL
         AND frozen_pdf_path IS NOT NULL
         AND frozen_pdf_sha256 IS NOT NULL
         AND page_count IS NOT NULL
         AND page_count > 0)
        OR status <> 'frozen'
    ),
    UNIQUE(draft_id, student_id, series_version),
    FOREIGN KEY(draft_id)
        REFERENCES personalized_recommendation_drafts(draft_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS personalized_paper_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_instance_id TEXT NOT NULL,
    task_item_code TEXT NOT NULL UNIQUE,
    item_order INTEGER NOT NULL CHECK (item_order >= 1),
    bank_question_id INTEGER,
    question_content_hash TEXT NOT NULL,
    question_snapshot_json TEXT NOT NULL,
    criterion_version_id TEXT NOT NULL,
    criterion_hash TEXT NOT NULL,
    criterion_snapshot_json TEXT NOT NULL,
    recommendation_snapshot_json TEXT NOT NULL,
    CHECK (length(question_content_hash) = 64),
    CHECK (length(criterion_version_id) = 64),
    CHECK (length(criterion_hash) = 64),
    CHECK (json_valid(question_snapshot_json)),
    CHECK (json_valid(criterion_snapshot_json)),
    CHECK (json_valid(recommendation_snapshot_json)),
    UNIQUE(paper_instance_id, item_order),
    FOREIGN KEY(paper_instance_id)
        REFERENCES personalized_paper_instances(paper_instance_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS personalized_paper_pages (
    page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_instance_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    total_pages INTEGER NOT NULL CHECK (total_pages >= 1),
    page_identity TEXT NOT NULL UNIQUE,
    page_signature TEXT NOT NULL,
    page_content_hash TEXT NOT NULL,
    layout_version TEXT NOT NULL,
    pdf_sha256 TEXT NOT NULL,
    width_points REAL NOT NULL CHECK (width_points > 0),
    height_points REAL NOT NULL CHECK (height_points > 0),
    CHECK (page_number <= total_pages),
    CHECK (length(page_signature) = 32),
    CHECK (length(page_content_hash) = 64),
    CHECK (length(pdf_sha256) = 64),
    UNIQUE(paper_instance_id, page_number),
    FOREIGN KEY(paper_instance_id)
        REFERENCES personalized_paper_instances(paper_instance_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS personalized_paper_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_instance_id TEXT NOT NULL,
    operation_token TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('created', 'frozen')),
    actor_ref TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    details_json TEXT NOT NULL,
    resulting_instance_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(operation_token) = 32),
    CHECK (length(operation_fingerprint) = 64),
    CHECK (json_valid(details_json)),
    CHECK (json_valid(resulting_instance_json)),
    FOREIGN KEY(paper_instance_id)
        REFERENCES personalized_paper_instances(paper_instance_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personalized_paper_draft_student
ON personalized_paper_instances(draft_id, student_id, series_version);

CREATE INDEX IF NOT EXISTS idx_personalized_paper_status
ON personalized_paper_instances(status, updated_at, paper_instance_id);

CREATE INDEX IF NOT EXISTS idx_personalized_paper_page_instance
ON personalized_paper_pages(paper_instance_id, page_number);
