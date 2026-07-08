-- 阅卷库 Schema 基线（000）：与运行时初始化等价的幂等 DDL。
-- 由 tools/generate_schema_baseline.py 于 2026-07-03 22:38 生成，请勿手工编辑；
-- 运行时 DDL 实质变化时重跑生成器重建本文件（漂移由 tests/test_schema_baseline.py 守卫）。

CREATE TABLE IF NOT EXISTS annotated_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    result_id INTEGER NOT NULL UNIQUE,
                    annotated_front_path TEXT,
                    annotated_back_path TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(result_id) REFERENCES session_results(id)
                );

CREATE TABLE IF NOT EXISTS answer_regions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    region_uuid TEXT NOT NULL UNIQUE,
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
                    mapping_status TEXT NOT NULL DEFAULT 'unbound',
                    multi_region_confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(template_id) REFERENCES session_templates(id)
                );

CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

CREATE TABLE IF NOT EXISTS exam_papers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    front_image TEXT NOT NULL,
                    back_image TEXT NOT NULL,
                    ocr_name TEXT,
                    student_id INTEGER,
                    match_status TEXT NOT NULL,
                    processing_status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(student_id) REFERENCES students(id)
                );

CREATE TABLE IF NOT EXISTS exam_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_name TEXT NOT NULL,
                    front_image TEXT NOT NULL,
                    back_image TEXT NOT NULL,
                    total_score REAL NOT NULL,
                    student_score REAL NOT NULL,
                    needs_human_review INTEGER NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

CREATE TABLE IF NOT EXISTS grading_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exam_result_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    score_awarded REAL NOT NULL,
                    deduction_reason TEXT,
                    knowledge_id TEXT NOT NULL,
                    knowledge_ids TEXT,
                    error_category TEXT,
                    error_summary TEXT, confidence_score REAL,
                    FOREIGN KEY(exam_result_id) REFERENCES exam_results(id)
                );

CREATE TABLE IF NOT EXISTS grading_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER,
    student_id INTEGER NOT NULL,
    source_label TEXT NOT NULL,
    paper_fingerprint TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending','grading','graded','failed',
            'skipped_existing','skipped_duplicate','conflict'
        )
    ),
    disposition_reason TEXT,
    result_id INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(run_id, source_label),
    FOREIGN KEY(run_id) REFERENCES grading_runs(id),
    FOREIGN KEY(paper_id) REFERENCES exam_papers(id),
    FOREIGN KEY(student_id) REFERENCES students(id),
    FOREIGN KEY(result_id) REFERENCES session_results(id)
);

CREATE TABLE IF NOT EXISTS grading_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_token TEXT NOT NULL UNIQUE,
    session_id INTEGER NOT NULL,
    config_fingerprint TEXT NOT NULL,
    grading_mode TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('running','pause_requested','paused','completed','failed')
    ),
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    FOREIGN KEY(session_id) REFERENCES grading_sessions(id)
);

CREATE TABLE IF NOT EXISTS grading_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_name TEXT NOT NULL,
                    rubric_path TEXT NOT NULL,
                    answer_key_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                , template_config_path TEXT, source_paper_path TEXT, source_paper_sha256 TEXT, question_bank_sync_state TEXT NOT NULL DEFAULT 'not_started', question_bank_sync_details_json TEXT NOT NULL DEFAULT '{}', question_bank_sync_error TEXT, question_bank_sync_updated_at TEXT);

CREATE TABLE IF NOT EXISTS session_attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    attendance_status TEXT NOT NULL,
                    source_reason TEXT,
                    matched_paper_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    UNIQUE(session_id, student_id),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(student_id) REFERENCES students(id),
                    FOREIGN KEY(matched_paper_id) REFERENCES exam_papers(id)
                );

CREATE TABLE IF NOT EXISTS session_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    score_awarded REAL NOT NULL,
                    deduction_reason TEXT,
                    knowledge_id TEXT NOT NULL,
                    knowledge_ids TEXT,
                    error_category TEXT,
                    error_summary TEXT, confidence_score REAL, secondary_errors_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY(result_id) REFERENCES session_results(id)
                );

CREATE TABLE IF NOT EXISTS session_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    paper_id INTEGER NOT NULL,
                    total_score REAL NOT NULL,
                    student_score REAL NOT NULL,
                    needs_human_review INTEGER NOT NULL,
                    raw_json TEXT NOT NULL,
                    graded_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(student_id) REFERENCES students(id),
                    FOREIGN KEY(paper_id) REFERENCES exam_papers(id)
                );

CREATE TABLE IF NOT EXISTS session_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL UNIQUE,
                    front_template_path TEXT NOT NULL,
                    back_template_path TEXT NOT NULL,
                    ai_analysis_path TEXT,
                    template_config_path TEXT,
                    regions_path TEXT,
                    is_confirmed INTEGER NOT NULL DEFAULT 0,
                    regions_snapshot_pending INTEGER NOT NULL DEFAULT 0,
                    regions_snapshot_token TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id)
                );

CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    class_name TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

CREATE UNIQUE INDEX IF NOT EXISTS idx_answer_regions_region_uuid_unique ON answer_regions(region_uuid);

CREATE INDEX IF NOT EXISTS idx_exam_papers_session_status ON exam_papers(session_id, processing_status);

CREATE INDEX IF NOT EXISTS idx_exam_papers_student ON exam_papers(student_id);

CREATE INDEX IF NOT EXISTS idx_grading_details_exam_result ON grading_details(exam_result_id);

CREATE INDEX IF NOT EXISTS idx_grading_run_items_identity
ON grading_run_items(student_id, paper_fingerprint, config_fingerprint, status);

CREATE INDEX IF NOT EXISTS idx_grading_runs_session_state
ON grading_runs(session_id, state, id);

CREATE INDEX IF NOT EXISTS idx_grading_sessions_active ON grading_sessions(is_deleted, status, updated_at);

CREATE INDEX IF NOT EXISTS idx_session_attendance_session_status ON session_attendance(session_id, attendance_status);

CREATE INDEX IF NOT EXISTS idx_session_details_question ON session_details(question_id);

CREATE INDEX IF NOT EXISTS idx_session_details_result ON session_details(result_id);

CREATE INDEX IF NOT EXISTS idx_session_results_paper ON session_results(paper_id);

CREATE INDEX IF NOT EXISTS idx_session_results_session_student ON session_results(session_id, student_id);

CREATE INDEX IF NOT EXISTS idx_students_class_name ON students(class_name);

CREATE TRIGGER IF NOT EXISTS answer_regions_region_uuid_required_insert
                BEFORE INSERT ON answer_regions
                WHEN NEW.region_uuid IS NULL OR TRIM(NEW.region_uuid) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'answer_regions.region_uuid must be nonblank');
                END;

CREATE TRIGGER IF NOT EXISTS answer_regions_region_uuid_required_update
                BEFORE UPDATE OF region_uuid ON answer_regions
                WHEN NEW.region_uuid IS NULL OR TRIM(NEW.region_uuid) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'answer_regions.region_uuid must be nonblank');
                END;
