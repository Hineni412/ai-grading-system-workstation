CREATE TABLE curriculum_editions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    title TEXT NOT NULL
        CHECK(length(trim(title)) BETWEEN 1 AND 160),
    subject TEXT NOT NULL DEFAULT 'math'
        CHECK(subject = 'math'),
    grade_level INTEGER NOT NULL
        CHECK(grade_level BETWEEN 7 AND 9),
    volume TEXT NOT NULL
        CHECK(volume IN ('first', 'second', 'whole_year')),
    publisher TEXT,
    edition_label TEXT,
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

CREATE TABLE lesson_nodes (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    curriculum_id TEXT NOT NULL,
    parent_id TEXT,
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    node_type TEXT NOT NULL
        CHECK(node_type IN ('chapter', 'section', 'lesson')),
    title TEXT NOT NULL
        CHECK(length(trim(title)) BETWEEN 1 AND 160),
    sort_order INTEGER NOT NULL
        CHECK(sort_order > 0),
    duration_minutes INTEGER
        CHECK(duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 300),
    source_kind TEXT NOT NULL DEFAULT 'teacher'
        CHECK(source_kind IN ('teacher', 'catalog', 'assistant_draft')),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK(is_active IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(id, curriculum_id),
    FOREIGN KEY(curriculum_id)
        REFERENCES curriculum_editions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(parent_id, curriculum_id)
        REFERENCES lesson_nodes(id, curriculum_id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_lesson_nodes_root_order
ON lesson_nodes(curriculum_id, sort_order)
WHERE parent_id IS NULL;

CREATE UNIQUE INDEX uq_lesson_nodes_child_order
ON lesson_nodes(parent_id, sort_order)
WHERE parent_id IS NOT NULL;

CREATE INDEX idx_lesson_nodes_tree
ON lesson_nodes(curriculum_id, parent_id, is_active, sort_order);

CREATE TABLE material_sources (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    display_name TEXT NOT NULL
        CHECK(length(trim(display_name)) BETWEEN 1 AND 200),
    material_type TEXT NOT NULL
        CHECK(material_type IN ('pdf', 'pptx', 'image')),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

CREATE TABLE material_versions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    source_id TEXT NOT NULL,
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    content_sha256 TEXT NOT NULL UNIQUE
        CHECK(length(content_sha256) = 64),
    file_name TEXT NOT NULL
        CHECK(length(trim(file_name)) BETWEEN 1 AND 240),
    size_bytes INTEGER NOT NULL
        CHECK(size_bytes >= 0),
    modified_ns INTEGER
        CHECK(modified_ns IS NULL OR modified_ns >= 0),
    unit_count INTEGER
        CHECK(unit_count IS NULL OR unit_count >= 0),
    inspection_status TEXT NOT NULL
        CHECK(inspection_status IN (
            'uninspected',
            'ready',
            'scanned_no_text',
            'encrypted',
            'damaged',
            'locked'
        )),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(source_id)
        REFERENCES material_sources(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_material_versions_source
ON material_versions(source_id, created_at DESC);

CREATE TABLE material_locations (
    version_id TEXT PRIMARY KEY,
    local_path TEXT NOT NULL,
    availability TEXT NOT NULL
        CHECK(availability IN ('available', 'missing', 'needs_relocation')),
    last_checked_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(version_id)
        REFERENCES material_versions(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_material_locations_availability
ON material_locations(availability, last_checked_at);
