CREATE TABLE reference_ppt_collections (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    semester_id TEXT NOT NULL,
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    display_name TEXT NOT NULL
        CHECK(length(trim(display_name)) BETWEEN 1 AND 160),
    mapping_proposal_id TEXT NOT NULL UNIQUE,
    ignored_file_count INTEGER NOT NULL DEFAULT 0
        CHECK(ignored_file_count >= 0),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(semester_id)
        REFERENCES teaching_semesters(id)
        ON DELETE CASCADE,
    FOREIGN KEY(mapping_proposal_id)
        REFERENCES semester_mapping_proposals(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_reference_ppt_collections_semester
ON reference_ppt_collections(semester_id, created_at DESC);

CREATE TABLE reference_ppt_collection_members (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    collection_id TEXT NOT NULL,
    material_record_id TEXT NOT NULL,
    relative_path TEXT NOT NULL
        CHECK(length(trim(relative_path)) BETWEEN 1 AND 600),
    kind TEXT NOT NULL
        CHECK(kind IN ('lesson', 'review', 'strategy', 'practice')),
    confidence TEXT NOT NULL
        CHECK(confidence IN ('high', 'medium', 'low')),
    chapter_number INTEGER,
    section_number INTEGER,
    subsection_number INTEGER,
    lesson_number INTEGER,
    normalized_title TEXT NOT NULL
        CHECK(length(trim(normalized_title)) BETWEEN 1 AND 200),
    evidence_json TEXT NOT NULL DEFAULT '[]'
        CHECK(json_valid(evidence_json)),
    issues_json TEXT NOT NULL DEFAULT '[]'
        CHECK(json_valid(issues_json)),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(collection_id, material_record_id),
    UNIQUE(collection_id, relative_path),
    FOREIGN KEY(collection_id)
        REFERENCES reference_ppt_collections(id)
        ON DELETE CASCADE,
    FOREIGN KEY(material_record_id)
        REFERENCES semester_material_records(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_reference_ppt_collection_members_identity
ON reference_ppt_collection_members(
    collection_id,
    chapter_number,
    section_number,
    lesson_number
);
