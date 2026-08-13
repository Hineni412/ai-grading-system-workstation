CREATE TABLE IF NOT EXISTS knowledge_concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    subject TEXT,
    grade TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS knowledge_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_concept_id INTEGER NOT NULL,
    target_concept_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL
        CHECK (relation_type IN ('prerequisite', 'related', 'parent')),
    weight REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0 AND weight <= 1.0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(source_concept_id, target_concept_id, relation_type),
    FOREIGN KEY(source_concept_id) REFERENCES knowledge_concepts(id),
    FOREIGN KEY(target_concept_id) REFERENCES knowledge_concepts(id)
);

CREATE TABLE IF NOT EXISTS knowledge_source_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_namespace TEXT NOT NULL,
    source_value TEXT NOT NULL,
    normalized_value TEXT NOT NULL DEFAULT '',
    concept_id INTEGER,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('confirmed', 'suggested', 'rejected')),
    confidence REAL NOT NULL DEFAULT 0.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(source_namespace, source_value),
    FOREIGN KEY(concept_id) REFERENCES knowledge_concepts(id)
);

CREATE TABLE IF NOT EXISTS grading_question_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grading_session_id TEXT NOT NULL,
    source_question_id TEXT NOT NULL,
    bank_question_id INTEGER NOT NULL,
    link_method TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('confirmed', 'suggested', 'rejected')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(grading_session_id, source_question_id),
    FOREIGN KEY(bank_question_id) REFERENCES questions(id)
);

CREATE TABLE IF NOT EXISTS training_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_code TEXT NOT NULL UNIQUE,
    created_by TEXT,
    scope_json TEXT NOT NULL DEFAULT '{}',
    exam_scope_json TEXT NOT NULL DEFAULT '{}',
    diagnosis_snapshot_json TEXT NOT NULL DEFAULT '{}',
    generation_config_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'ready', 'exporting', 'completed', 'cancelled', 'failed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS training_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    variant_key TEXT NOT NULL,
    variant_type TEXT NOT NULL
        CHECK (variant_type IN ('individual', 'group')),
    grouping_reason_json TEXT NOT NULL DEFAULT '{}',
    diagnosis_snapshot_json TEXT NOT NULL DEFAULT '{}',
    shortages_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'exporting', 'completed', 'failed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(task_id, variant_key),
    FOREIGN KEY(task_id) REFERENCES training_tasks(id)
);

CREATE TABLE IF NOT EXISTS variant_students (
    variant_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    student_name_snapshot TEXT,
    class_id_snapshot TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY(variant_id, student_id),
    FOREIGN KEY(variant_id) REFERENCES training_variants(id)
);

CREATE TABLE IF NOT EXISTS training_task_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id INTEGER NOT NULL,
    task_item_code TEXT NOT NULL UNIQUE,
    bank_question_id INTEGER,
    bank_question_fingerprint TEXT,
    item_order INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL
        CHECK (stage IN ('direct', 'prerequisite', 'transfer')),
    concept_snapshot_json TEXT NOT NULL DEFAULT '{}',
    recommendation_snapshot_json TEXT NOT NULL DEFAULT '{}',
    question_snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(variant_id, item_order),
    FOREIGN KEY(variant_id) REFERENCES training_variants(id),
    FOREIGN KEY(bank_question_id) REFERENCES questions(id)
);

CREATE TABLE IF NOT EXISTS training_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    variant_id INTEGER,
    audience TEXT NOT NULL
        CHECK (audience IN ('student', 'teacher', 'bundle')),
    export_format TEXT NOT NULL,
    output_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'succeeded', 'failed')),
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(task_id) REFERENCES training_tasks(id),
    FOREIGN KEY(variant_id) REFERENCES training_variants(id)
);

CREATE TABLE IF NOT EXISTS training_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_item_code TEXT NOT NULL,
    student_id TEXT NOT NULL,
    grading_session_id TEXT,
    grading_question_id TEXT,
    score_awarded REAL,
    full_score REAL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(task_item_code, student_id, grading_session_id, grading_question_id),
    FOREIGN KEY(task_item_code) REFERENCES training_task_items(task_item_code)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
    ON knowledge_relations(source_concept_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
    ON knowledge_relations(target_concept_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_source_mappings_lookup
    ON knowledge_source_mappings(source_namespace, normalized_value, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_source_mappings_concept
    ON knowledge_source_mappings(concept_id, status);
CREATE INDEX IF NOT EXISTS idx_grading_question_links_session
    ON grading_question_links(grading_session_id, status);
CREATE INDEX IF NOT EXISTS idx_grading_question_links_bank_question
    ON grading_question_links(bank_question_id, status);
CREATE INDEX IF NOT EXISTS idx_training_tasks_status
    ON training_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_training_variants_task
    ON training_variants(task_id, status);
CREATE INDEX IF NOT EXISTS idx_variant_students_student
    ON variant_students(student_id, variant_id);
CREATE INDEX IF NOT EXISTS idx_training_task_items_variant
    ON training_task_items(variant_id, item_order);
CREATE INDEX IF NOT EXISTS idx_training_task_items_question
    ON training_task_items(bank_question_id);
CREATE INDEX IF NOT EXISTS idx_training_exports_task
    ON training_exports(task_id, status);
CREATE INDEX IF NOT EXISTS idx_training_exports_variant
    ON training_exports(variant_id, status);
CREATE INDEX IF NOT EXISTS idx_training_attempts_student
    ON training_attempts(student_id, created_at);
