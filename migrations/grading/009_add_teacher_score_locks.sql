-- P3.5: preserve teacher-confirmed scores independently from disposable AI run data.
CREATE TABLE teacher_score_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    scan_batch_id TEXT NOT NULL
        CHECK (TRIM(scan_batch_id) <> ''),
    student_id INTEGER NOT NULL,
    question_id TEXT NOT NULL
        CHECK (TRIM(question_id) <> ''),
    score_awarded REAL NOT NULL
        CHECK (
            typeof(score_awarded) IN ('integer', 'real')
            AND score_awarded >= 0
            AND score_awarded <= max_score
        ),
    max_score REAL NOT NULL
        CHECK (
            typeof(max_score) IN ('integer', 'real')
            AND max_score > 0
        ),
    deduction_reason TEXT,
    source_target_type TEXT NOT NULL
        CHECK (TRIM(source_target_type) <> ''),
    source_target_id INTEGER NOT NULL
        CHECK (
            typeof(source_target_id) = 'integer'
            AND source_target_id > 0
        ),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK (
            typeof(revision) = 'integer'
            AND revision >= 1
        ),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(session_id, scan_batch_id, student_id, question_id),
    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
);

CREATE INDEX idx_teacher_score_locks_session_batch_student
ON teacher_score_locks(session_id, scan_batch_id, student_id);

CREATE INDEX idx_teacher_score_locks_source_target
ON teacher_score_locks(source_target_type, source_target_id);
