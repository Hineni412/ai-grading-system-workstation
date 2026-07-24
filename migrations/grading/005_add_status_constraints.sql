-- migration-policy: rebuild-tables grading_sessions,exam_papers,answer_regions
-- P3-12: constrain the four persistent grading state columns.

-- These columns were historically added by runtime initializers.  Old
-- untracked databases may not have them; duplicate-column errors are a
-- migration-runner-supported no-op, and every addition stays in this
-- migration's transaction.
ALTER TABLE grading_sessions ADD COLUMN source_paper_path TEXT;
ALTER TABLE grading_sessions ADD COLUMN source_paper_sha256 TEXT;
ALTER TABLE grading_sessions ADD COLUMN question_bank_sync_state
    TEXT NOT NULL DEFAULT 'not_started';
ALTER TABLE grading_sessions ADD COLUMN question_bank_sync_details_json
    TEXT NOT NULL DEFAULT '{}';
ALTER TABLE grading_sessions ADD COLUMN question_bank_sync_error TEXT;
ALTER TABLE grading_sessions ADD COLUMN question_bank_sync_updated_at TEXT;

CREATE TABLE grading_sessions_p3_12_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT NOT NULL,
    rubric_path TEXT NOT NULL,
    answer_key_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'running', 'completed', 'failed')),
    is_deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    template_config_path TEXT,
    source_paper_path TEXT,
    source_paper_sha256 TEXT,
    question_bank_sync_state TEXT NOT NULL DEFAULT 'not_started',
    question_bank_sync_details_json TEXT NOT NULL DEFAULT '{}',
    question_bank_sync_error TEXT,
    question_bank_sync_updated_at TEXT
);

CREATE TABLE exam_papers_p3_12_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    front_image TEXT NOT NULL,
    back_image TEXT NOT NULL,
    ocr_name TEXT,
    student_id INTEGER,
    match_status TEXT NOT NULL
        CHECK (match_status IN ('matched', 'unmatched', 'student_deleted')),
    processing_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            processing_status IN (
                'pending', 'grading', 'graded', 'failed', 'skipped'
            )
        ),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
    FOREIGN KEY(student_id) REFERENCES students(id)
);

CREATE TABLE answer_regions_p3_12_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region_uuid TEXT,
    session_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    page TEXT NOT NULL,
    region_order INTEGER NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    w INTEGER NOT NULL,
    h INTEGER NOT NULL,
    detected_question_id TEXT,
    mapped_question_id TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    is_confirmed INTEGER NOT NULL DEFAULT 0,
    mapping_status TEXT NOT NULL DEFAULT 'unbound'
        CHECK (mapping_status IN ('unbound', 'auto', 'manual')),
    multi_region_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
    FOREIGN KEY(template_id) REFERENCES session_templates(id)
);

INSERT INTO grading_sessions_p3_12_new (
    id, session_name, rubric_path, answer_key_path, status, is_deleted,
    deleted_at, created_at, updated_at, template_config_path,
    source_paper_path, source_paper_sha256, question_bank_sync_state,
    question_bank_sync_details_json, question_bank_sync_error,
    question_bank_sync_updated_at
)
SELECT
    id, session_name, rubric_path, answer_key_path, status, is_deleted,
    deleted_at, created_at, updated_at, template_config_path,
    source_paper_path, source_paper_sha256, question_bank_sync_state,
    question_bank_sync_details_json, question_bank_sync_error,
    question_bank_sync_updated_at
FROM grading_sessions;

INSERT INTO exam_papers_p3_12_new (
    id, session_id, front_image, back_image, ocr_name, student_id,
    match_status, processing_status, error_message, created_at
)
SELECT
    id, session_id, front_image, back_image, ocr_name, student_id,
    match_status, processing_status, error_message, created_at
FROM exam_papers;

INSERT INTO answer_regions_p3_12_new (
    id, region_uuid, session_id, template_id, page, region_order,
    x, y, w, h, detected_question_id, mapped_question_id,
    confidence, is_confirmed, mapping_status, multi_region_confirmed,
    created_at, updated_at
)
SELECT
    id, region_uuid, session_id, template_id, page, region_order,
    x, y, w, h, detected_question_id, mapped_question_id,
    confidence, is_confirmed, mapping_status, multi_region_confirmed,
    created_at, updated_at
FROM answer_regions;

DROP TABLE answer_regions;
DROP TABLE exam_papers;
DROP TABLE grading_sessions;

ALTER TABLE grading_sessions_p3_12_new RENAME TO grading_sessions;
ALTER TABLE exam_papers_p3_12_new RENAME TO exam_papers;
ALTER TABLE answer_regions_p3_12_new RENAME TO answer_regions;

CREATE INDEX idx_grading_sessions_active
ON grading_sessions(is_deleted, status, updated_at);

CREATE INDEX idx_exam_papers_session_status
ON exam_papers(session_id, processing_status);

CREATE INDEX idx_exam_papers_student
ON exam_papers(student_id);

CREATE UNIQUE INDEX idx_answer_regions_region_uuid_unique
ON answer_regions(region_uuid);

CREATE TRIGGER answer_regions_region_uuid_required_insert
BEFORE INSERT ON answer_regions
WHEN NEW.region_uuid IS NULL OR TRIM(NEW.region_uuid) = ''
BEGIN
    SELECT RAISE(ABORT, 'answer_regions.region_uuid must be nonblank');
END;

CREATE TRIGGER answer_regions_region_uuid_required_update
BEFORE UPDATE OF region_uuid ON answer_regions
WHEN NEW.region_uuid IS NULL OR TRIM(NEW.region_uuid) = ''
BEGIN
    SELECT RAISE(ABORT, 'answer_regions.region_uuid must be nonblank');
END;
