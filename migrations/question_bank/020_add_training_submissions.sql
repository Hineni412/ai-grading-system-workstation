CREATE TABLE IF NOT EXISTS training_scan_batches (
    batch_id TEXT PRIMARY KEY,
    operation_token TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    paper_batch_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'manual_review'
        CHECK (status IN ('manual_review', 'ready', 'cancelled')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(batch_id) = 64),
    CHECK (length(operation_token) = 32),
    CHECK (length(operation_fingerprint) = 64),
    CHECK (length(paper_batch_id) = 64)
);

CREATE TABLE IF NOT EXISTS training_submissions (
    submission_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    paper_instance_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'manual_review'
        CHECK (status IN ('manual_review', 'ready', 'cancelled')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    expected_total_pages INTEGER NOT NULL CHECK (expected_total_pages >= 1),
    assessment_started_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(batch_id, paper_instance_id),
    FOREIGN KEY(batch_id) REFERENCES training_scan_batches(batch_id)
        ON DELETE CASCADE,
    FOREIGN KEY(paper_instance_id)
        REFERENCES personalized_paper_instances(paper_instance_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_submission_uploads (
    upload_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    operation_token TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    page_count INTEGER NOT NULL CHECK (page_count >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(upload_id) = 64),
    CHECK (length(operation_token) = 32),
    CHECK (length(operation_fingerprint) = 64),
    CHECK (length(content_sha256) = 64),
    UNIQUE(batch_id, content_sha256),
    FOREIGN KEY(batch_id) REFERENCES training_scan_batches(batch_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS training_submission_pages (
    scan_page_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    upload_id TEXT NOT NULL,
    upload_page_number INTEGER NOT NULL CHECK (upload_page_number >= 1),
    submission_id TEXT,
    paper_instance_id TEXT,
    claimed_page_number INTEGER,
    claimed_total_pages INTEGER,
    page_identity TEXT,
    image_sha256 TEXT NOT NULL,
    image_fingerprint TEXT NOT NULL,
    image_path TEXT NOT NULL,
    width_pixels INTEGER NOT NULL CHECK (width_pixels > 0),
    height_pixels INTEGER NOT NULL CHECK (height_pixels > 0),
    blur_score REAL NOT NULL,
    brightness_score REAL NOT NULL,
    contrast_score REAL NOT NULL,
    rotation_degrees INTEGER NOT NULL DEFAULT 0,
    issue_code TEXT,
    state TEXT NOT NULL
        CHECK (state IN ('assigned', 'unassigned', 'duplicate', 'conflict',
                         'replaced', 'dismissed')),
    assignment_revision INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(scan_page_id) = 64),
    CHECK (length(image_sha256) = 64),
    CHECK (length(image_fingerprint) = 16),
    CHECK (claimed_page_number IS NULL OR claimed_page_number >= 1),
    CHECK (claimed_total_pages IS NULL OR claimed_total_pages >= 1),
    CHECK (rotation_degrees IN (0, 90, 180, 270)),
    UNIQUE(upload_id, upload_page_number),
    FOREIGN KEY(batch_id) REFERENCES training_scan_batches(batch_id)
        ON DELETE CASCADE,
    FOREIGN KEY(upload_id) REFERENCES training_submission_uploads(upload_id)
        ON DELETE CASCADE,
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE SET NULL,
    FOREIGN KEY(paper_instance_id)
        REFERENCES personalized_paper_instances(paper_instance_id)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS training_submission_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    submission_id TEXT,
    operation_token TEXT NOT NULL UNIQUE,
    operation_fingerprint TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('batch_created', 'upload_ingested',
                              'page_matched', 'page_replaced',
                              'page_dismissed', 'submission_cancelled')),
    actor_ref TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(operation_token) = 32),
    CHECK (length(operation_fingerprint) = 64),
    CHECK (json_valid(details_json)),
    FOREIGN KEY(batch_id) REFERENCES training_scan_batches(batch_id)
        ON DELETE CASCADE,
    FOREIGN KEY(submission_id) REFERENCES training_submissions(submission_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_training_submission_batch
ON training_submissions(batch_id, status, paper_instance_id);

CREATE INDEX IF NOT EXISTS idx_training_submission_page_batch
ON training_submission_pages(batch_id, state, issue_code);

CREATE INDEX IF NOT EXISTS idx_training_submission_page_slot
ON training_submission_pages(submission_id, claimed_page_number, state);

CREATE INDEX IF NOT EXISTS idx_training_submission_upload_batch
ON training_submission_uploads(batch_id, created_at, upload_id);
