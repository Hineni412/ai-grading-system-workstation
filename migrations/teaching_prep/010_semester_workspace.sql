CREATE TABLE teaching_semesters (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    curriculum_id TEXT NOT NULL UNIQUE,
    school_year TEXT NOT NULL
        CHECK(length(trim(school_year)) BETWEEN 4 AND 20),
    term TEXT NOT NULL
        CHECK(term IN ('first', 'second')),
    planned_new_lesson_count INTEGER NOT NULL
        CHECK(planned_new_lesson_count BETWEEN 0 AND 500),
    status TEXT NOT NULL DEFAULT 'planning'
        CHECK(status IN ('planning', 'active', 'completed', 'archived')),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(curriculum_id)
        REFERENCES curriculum_editions(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_teaching_semesters_status
ON teaching_semesters(status, school_year DESC, term);

CREATE TABLE semester_lesson_progress (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    semester_id TEXT NOT NULL,
    lesson_node_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK(status IN (
            'not_started',
            'preparing',
            'ready',
            'taught',
            'skipped'
        )),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(semester_id, lesson_node_id),
    FOREIGN KEY(semester_id)
        REFERENCES teaching_semesters(id)
        ON DELETE CASCADE,
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_semester_lesson_progress_status
ON semester_lesson_progress(semester_id, status, updated_at);

CREATE TABLE semester_material_records (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    semester_id TEXT NOT NULL,
    material_source_id TEXT NOT NULL,
    material_role TEXT NOT NULL
        CHECK(material_role IN (
            'textbook',
            'reference_ppt',
            'exercise_workbook',
            'homework_workbook',
            'answer_book',
            'supplement'
        )),
    parse_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK(parse_status IN (
            'not_started',
            'parsed',
            'needs_review',
            'failed'
        )),
    mapping_status TEXT NOT NULL DEFAULT 'unmapped'
        CHECK(mapping_status IN (
            'unmapped',
            'proposed',
            'partial',
            'confirmed',
            'needs_review',
            'conflict'
        )),
    last_parsed_version_id TEXT,
    parsed_at TEXT,
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
    UNIQUE(semester_id, material_source_id),
    FOREIGN KEY(semester_id)
        REFERENCES teaching_semesters(id)
        ON DELETE CASCADE,
    FOREIGN KEY(material_source_id)
        REFERENCES material_sources(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(last_parsed_version_id)
        REFERENCES material_versions(id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_semester_active_homework_workbook
ON semester_material_records(semester_id)
WHERE material_role = 'homework_workbook' AND is_active = 1;

CREATE INDEX idx_semester_material_records_status
ON semester_material_records(
    semester_id,
    is_active,
    parse_status,
    mapping_status
);
