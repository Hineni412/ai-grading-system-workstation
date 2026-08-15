CREATE TABLE slide_animation_runs (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    lesson_node_id TEXT NOT NULL,
    material_version_id TEXT NOT NULL,
    material_link_id TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    page_indexes_json TEXT NOT NULL,
    storyboard_json TEXT,
    html_relpath TEXT,
    html_sha256 TEXT
        CHECK(html_sha256 IS NULL OR length(html_sha256) = 64),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN (
            'running',
            'succeeded',
            'accepted',
            'discarded',
            'failed',
            'cancelled',
            'result_unknown'
        )),
    teacher_decision TEXT NOT NULL DEFAULT 'pending'
        CHECK(teacher_decision IN ('pending', 'accepted', 'discarded')),
    error_code TEXT,
    model_call_count INTEGER NOT NULL DEFAULT 0
        CHECK(model_call_count IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    finished_at TEXT,
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE CASCADE,
    FOREIGN KEY(material_version_id)
        REFERENCES material_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(material_link_id)
        REFERENCES lesson_material_links(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_slide_animation_runs_lesson
ON slide_animation_runs(lesson_node_id, created_at DESC);
