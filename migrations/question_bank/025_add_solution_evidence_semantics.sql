CREATE TABLE IF NOT EXISTS fine_term_core_mappings (
    mapping_id TEXT PRIMARY KEY,
    fine_term_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested', 'confirmed', 'rejected', 'retired')),
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('builtin', 'synthetic', 'model', 'teacher', 'import')),
    source_reference TEXT NOT NULL,
    rationale TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    decision_by TEXT,
    decision_note TEXT,
    decided_at TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(mapping_id) = 64),
    CHECK (TRIM(fine_term_id) <> ''),
    CHECK (TRIM(source_reference) <> ''),
    CHECK (TRIM(rationale) <> ''),
    CHECK (
        source_kind <> 'model'
        OR (
            model_name IS NOT NULL AND TRIM(model_name) <> ''
            AND model_version IS NOT NULL AND TRIM(model_version) <> ''
        )
    ),
    CHECK (
        (status = 'suggested' AND decision_by IS NULL AND decided_at IS NULL)
        OR (
            status <> 'suggested'
            AND decision_by IS NOT NULL AND TRIM(decision_by) <> ''
            AND decided_at IS NOT NULL
        )
    ),
    UNIQUE(fine_term_id, stable_key),
    FOREIGN KEY(stable_key) REFERENCES knowledge_tag_identities(stable_key)
);

CREATE TABLE IF NOT EXISTS fine_term_core_mapping_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('suggested', 'confirmed', 'rejected', 'retired', 'restored')),
    from_status TEXT
        CHECK (
            from_status IS NULL
            OR from_status IN ('suggested', 'confirmed', 'rejected', 'retired')
        ),
    to_status TEXT NOT NULL
        CHECK (to_status IN ('suggested', 'confirmed', 'rejected', 'retired')),
    actor_kind TEXT NOT NULL
        CHECK (actor_kind IN ('system', 'model', 'teacher', 'import')),
    actor_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(actor_ref) <> ''),
    CHECK (TRIM(reason) <> ''),
    UNIQUE(mapping_id, resulting_revision),
    FOREIGN KEY(mapping_id) REFERENCES fine_term_core_mappings(mapping_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS question_solution_evidence_versions (
    evidence_version_id TEXT PRIMARY KEY,
    question_id INTEGER NOT NULL,
    source_content_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'question-solution-evidence-v1'),
    content_hash TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded', 'stale')),
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('combined_model', 'teacher_manual', 'import', 'backfill')),
    source_reference TEXT NOT NULL,
    created_by TEXT NOT NULL,
    decision_by TEXT,
    decision_note TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(evidence_version_id) = 64),
    CHECK (length(source_content_hash) = 64),
    CHECK (length(content_hash) = 64),
    CHECK (json_valid(evidence_json)),
    CHECK (TRIM(source_reference) <> ''),
    CHECK (TRIM(created_by) <> ''),
    UNIQUE(question_id, source_kind, source_reference),
    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_fine_term_core_resolution
ON fine_term_core_mappings(fine_term_id, status, stable_key);

CREATE INDEX IF NOT EXISTS idx_fine_term_core_review
ON fine_term_core_mappings(status, source_kind, updated_at, mapping_id);

CREATE INDEX IF NOT EXISTS idx_fine_term_core_mapping_events
ON fine_term_core_mapping_events(mapping_id, resulting_revision);

CREATE INDEX IF NOT EXISTS idx_solution_evidence_question
ON question_solution_evidence_versions(
    question_id, status, source_content_hash, created_at
);
