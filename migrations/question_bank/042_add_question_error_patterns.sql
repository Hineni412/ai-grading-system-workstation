-- 题目典型错法（错因体系改造 P4）：只存教师确认后的条目；
-- 候选项留在考试侧 .class_analysis 状态文件，不写入本表。
CREATE TABLE IF NOT EXISTS question_error_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    category TEXT,
    pattern TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    trigger_kind TEXT NOT NULL DEFAULT 'observation'
        CHECK (trigger_kind IN ('option', 'wrong_answer', 'step', 'observation')),
    trigger_value TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('candidate', 'confirmed', 'merged', 'rejected')),
    source TEXT NOT NULL,
    occurrences_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(occurrences_json)),
    confirm_token TEXT,
    confirmed_by TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (TRIM(pattern) <> ''),
    CHECK (category IS NULL OR TRIM(category) <> ''),
    CHECK (TRIM(source) <> ''),
    UNIQUE(question_id, trigger_kind, trigger_value, pattern)
);

CREATE INDEX IF NOT EXISTS idx_question_error_patterns_question
ON question_error_patterns(question_id, status);
