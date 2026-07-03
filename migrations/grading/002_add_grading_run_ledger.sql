-- 批改运行账本：支持安全暂停/恢复、三元幂等判重与冲突记录。幂等创建。
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

CREATE INDEX IF NOT EXISTS idx_grading_runs_session_state
ON grading_runs(session_id, state, id);

CREATE INDEX IF NOT EXISTS idx_grading_run_items_identity
ON grading_run_items(student_id, paper_fingerprint, config_fingerprint, status);
