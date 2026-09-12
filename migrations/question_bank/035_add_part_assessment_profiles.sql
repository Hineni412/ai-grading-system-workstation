CREATE TABLE question_part_assessment_profiles (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    evidence_version_id TEXT NOT NULL,
    current_source_content_hash TEXT NOT NULL,
    source_type_alias TEXT NOT NULL DEFAULT '',
    parts_json TEXT NOT NULL CHECK (json_valid(parts_json)),
    revision INTEGER NOT NULL CHECK (revision > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(evidence_version_id) REFERENCES question_solution_evidence_versions(evidence_version_id),
    UNIQUE(question_id, revision)
);
CREATE UNIQUE INDEX idx_part_assessment_active
ON question_part_assessment_profiles(question_id) WHERE status = 'active';
