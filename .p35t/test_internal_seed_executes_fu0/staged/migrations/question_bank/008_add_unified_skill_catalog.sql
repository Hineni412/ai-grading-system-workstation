CREATE TABLE IF NOT EXISTS skill_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT 'math',
    grade_min INTEGER,
    grade_max INTEGER,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (grade_min IS NULL OR grade_min BETWEEN 1 AND 12),
    CHECK (grade_max IS NULL OR grade_max BETWEEN 1 AND 12),
    CHECK (grade_min IS NULL OR grade_max IS NULL OR grade_min <= grade_max)
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_key TEXT NOT NULL UNIQUE,
    topic_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    grade_min INTEGER,
    grade_max INTEGER,
    origin TEXT NOT NULL CHECK (origin IN ('builtin', 'local')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'merged', 'archived')),
    redirect_skill_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (grade_min IS NULL OR grade_min BETWEEN 1 AND 12),
    CHECK (grade_max IS NULL OR grade_max BETWEEN 1 AND 12),
    CHECK (grade_min IS NULL OR grade_max IS NULL OR grade_min <= grade_max),
    CHECK (redirect_skill_id IS NULL OR redirect_skill_id <> id),
    CHECK (
        (status = 'merged' AND redirect_skill_id IS NOT NULL)
        OR (status <> 'merged' AND redirect_skill_id IS NULL)
    ),
    FOREIGN KEY(topic_id) REFERENCES skill_topics(id),
    FOREIGN KEY(redirect_skill_id) REFERENCES skills(id)
);

CREATE TABLE IF NOT EXISTS assessment_item_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grading_session_id TEXT NOT NULL,
    source_question_id TEXT NOT NULL,
    skill_id INTEGER,
    role TEXT NOT NULL CHECK (role IN ('measured', 'supporting')),
    raw_knowledge_id TEXT,
    raw_knowledge_label TEXT,
    source TEXT NOT NULL DEFAULT 'resolver',
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'resolved' CHECK (status IN ('resolved', 'conflict')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(grading_session_id, source_question_id, skill_id, role),
    CHECK (
        (status = 'resolved' AND skill_id IS NOT NULL)
        OR (status = 'conflict' AND skill_id IS NULL)
    ),
    FOREIGN KEY(skill_id) REFERENCES skills(id)
);

CREATE TABLE IF NOT EXISTS question_skill_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    skill_id INTEGER,
    role TEXT NOT NULL CHECK (role IN ('measured', 'supporting')),
    raw_knowledge_id TEXT,
    raw_knowledge_label TEXT,
    source TEXT NOT NULL DEFAULT 'resolver',
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'resolved' CHECK (status IN ('resolved', 'conflict')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(question_id, skill_id, role),
    CHECK (
        (status = 'resolved' AND skill_id IS NOT NULL)
        OR (status = 'conflict' AND skill_id IS NULL)
    ),
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(skill_id) REFERENCES skills(id)
);

CREATE TABLE IF NOT EXISTS skill_resolution_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL CHECK (source_type IN ('assessment_item', 'question_bank_item', 'legacy_term')),
    source_ref TEXT NOT NULL,
    raw_label TEXT NOT NULL,
    normalized_label TEXT NOT NULL DEFAULT '',
    candidate_skill_ids_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved', 'ignored')),
    resolved_skill_id INTEGER,
    resolved_by TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (state <> 'resolved' OR resolved_skill_id IS NOT NULL),
    FOREIGN KEY(resolved_skill_id) REFERENCES skills(id)
);

CREATE TABLE IF NOT EXISTS skill_neighbors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_skill_id INTEGER NOT NULL,
    target_skill_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('same_topic', 'prerequisite', 'advanced', 'co_assessed')),
    weight REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0 AND weight <= 1.0),
    source TEXT NOT NULL DEFAULT 'builtin' CHECK (source IN ('builtin', 'statistical', 'admin')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(source_skill_id, target_skill_id, kind),
    CHECK (source_skill_id <> target_skill_id),
    FOREIGN KEY(source_skill_id) REFERENCES skills(id),
    FOREIGN KEY(target_skill_id) REFERENCES skills(id)
);

CREATE TABLE IF NOT EXISTS skill_system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS skill_migration_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'apply', 'rollback', 'set_mode')),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'rolled_back')),
    source_counts_json TEXT NOT NULL DEFAULT '{}',
    result_counts_json TEXT NOT NULL DEFAULT '{}',
    invariants_json TEXT NOT NULL DEFAULT '{}',
    report_path TEXT,
    backup_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_skill_topics_status ON skill_topics(subject, status, name);
CREATE INDEX IF NOT EXISTS idx_skills_topic_status ON skills(topic_id, status, name);
CREATE INDEX IF NOT EXISTS idx_skills_name_status ON skills(name, status);
CREATE INDEX IF NOT EXISTS idx_assessment_item_skills_source ON assessment_item_skills(grading_session_id, source_question_id, role, status);
CREATE INDEX IF NOT EXISTS idx_assessment_item_skills_skill ON assessment_item_skills(skill_id, role, status);
CREATE INDEX IF NOT EXISTS idx_question_skill_links_question ON question_skill_links(question_id, role, status);
CREATE INDEX IF NOT EXISTS idx_question_skill_links_skill ON question_skill_links(skill_id, role, status);
CREATE INDEX IF NOT EXISTS idx_skill_conflicts_open ON skill_resolution_conflicts(state, source_type, source_ref);
CREATE INDEX IF NOT EXISTS idx_skill_neighbors_source ON skill_neighbors(source_skill_id, enabled, kind, weight);
CREATE INDEX IF NOT EXISTS idx_skill_neighbors_target ON skill_neighbors(target_skill_id, enabled, kind);
CREATE INDEX IF NOT EXISTS idx_skill_migration_runs_status ON skill_migration_runs(status, started_at);

INSERT OR IGNORE INTO skill_system_settings (key, value)
VALUES ('recommendation_read_mode', 'legacy');
INSERT OR IGNORE INTO skill_system_settings (key, value)
VALUES ('catalog_version', 'junior_math_v1');
