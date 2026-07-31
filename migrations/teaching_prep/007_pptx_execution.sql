CREATE TABLE pptx_execution_runs (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    operation_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    slide_plan_id TEXT NOT NULL UNIQUE,
    source_material_version_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL
        CHECK(length(source_sha256) = 64),
    staging_name TEXT NOT NULL UNIQUE
        CHECK(length(staging_name) = 32),
    expected_slide_count INTEGER NOT NULL
        CHECK(expected_slide_count > 0),
    status TEXT NOT NULL
        CHECK(status IN (
            'running',
            'verifying',
            'publishing',
            'published',
            'failed',
            'cancelled',
            'interrupted'
        )),
    execution_report_json TEXT,
    verification_report_json TEXT,
    error_code TEXT,
    published_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    finished_at TEXT,
    FOREIGN KEY(operation_id)
        REFERENCES teaching_prep_operations(operation_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(slide_plan_id)
        REFERENCES slide_plan_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(source_material_version_id)
        REFERENCES material_versions(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_pptx_execution_runs_status
ON pptx_execution_runs(status, updated_at);

CREATE TABLE pptx_versions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    slide_plan_id TEXT NOT NULL UNIQUE,
    lesson_node_id TEXT NOT NULL,
    execution_run_id TEXT NOT NULL UNIQUE,
    version_number INTEGER NOT NULL
        CHECK(version_number > 0),
    status TEXT NOT NULL
        CHECK(status IN ('publishing', 'published', 'failed')),
    output_relpath TEXT NOT NULL UNIQUE,
    output_filename TEXT NOT NULL,
    output_sha256 TEXT,
    slide_count INTEGER NOT NULL
        CHECK(slide_count > 0),
    verification_report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    published_at TEXT,
    UNIQUE(lesson_node_id, version_number),
    FOREIGN KEY(slide_plan_id)
        REFERENCES slide_plan_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(execution_run_id)
        REFERENCES pptx_execution_runs(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_pptx_versions_lesson
ON pptx_versions(lesson_node_id, version_number DESC);
