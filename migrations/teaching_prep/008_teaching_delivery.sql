CREATE TABLE class_variants (
    id TEXT PRIMARY KEY CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    base_resource_pack_id TEXT NOT NULL,
    resource_pack_id TEXT NOT NULL UNIQUE,
    lesson_node_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    prior_review_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(base_resource_pack_id)
        REFERENCES resource_pack_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY(resource_pack_id)
        REFERENCES resource_pack_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id) ON DELETE RESTRICT
);

CREATE INDEX idx_class_variants_lesson
ON class_variants(lesson_node_id, created_at DESC);

CREATE TABLE up_class_packages (
    id TEXT PRIMARY KEY CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    pptx_version_id TEXT NOT NULL UNIQUE,
    slide_plan_id TEXT NOT NULL,
    lesson_draft_id TEXT NOT NULL,
    resource_pack_id TEXT NOT NULL,
    lesson_node_id TEXT NOT NULL,
    class_name TEXT,
    class_scope_key TEXT NOT NULL CHECK(length(class_scope_key) = 64),
    version_number INTEGER NOT NULL CHECK(version_number > 0),
    status TEXT NOT NULL CHECK(status IN (
        'building', 'publishing', 'complete', 'failed', 'interrupted'
    )),
    staging_name TEXT NOT NULL UNIQUE CHECK(length(staging_name) = 32),
    output_relpath TEXT,
    output_filename TEXT,
    package_sha256 TEXT,
    manifest_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    completed_at TEXT,
    UNIQUE(lesson_node_id, class_scope_key, version_number),
    FOREIGN KEY(pptx_version_id)
        REFERENCES pptx_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY(slide_plan_id)
        REFERENCES slide_plan_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY(lesson_draft_id)
        REFERENCES lesson_draft_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY(resource_pack_id)
        REFERENCES resource_pack_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id) ON DELETE RESTRICT
);

CREATE INDEX idx_up_class_packages_lesson
ON up_class_packages(lesson_node_id, class_scope_key, version_number DESC);

CREATE INDEX idx_up_class_packages_status
ON up_class_packages(status, updated_at);

CREATE TABLE up_class_package_selections (
    id TEXT PRIMARY KEY CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    lesson_node_id TEXT NOT NULL,
    class_scope_key TEXT NOT NULL CHECK(length(class_scope_key) = 64),
    package_id TEXT NOT NULL,
    previous_package_id TEXT,
    reason TEXT NOT NULL CHECK(reason IN ('published', 'rollback')),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id) ON DELETE RESTRICT,
    FOREIGN KEY(package_id)
        REFERENCES up_class_packages(id) ON DELETE RESTRICT,
    FOREIGN KEY(previous_package_id)
        REFERENCES up_class_packages(id) ON DELETE RESTRICT
);

CREATE INDEX idx_up_class_package_selections_scope
ON up_class_package_selections(
    lesson_node_id, class_scope_key, created_at DESC, id DESC
);

CREATE TABLE post_lesson_reviews (
    id TEXT PRIMARY KEY CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    package_id TEXT NOT NULL,
    pptx_version_id TEXT NOT NULL,
    lesson_node_id TEXT NOT NULL,
    class_name TEXT,
    payload_json TEXT NOT NULL,
    use_in_next_version INTEGER NOT NULL DEFAULT 1
        CHECK(use_in_next_version IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(package_id)
        REFERENCES up_class_packages(id) ON DELETE RESTRICT,
    FOREIGN KEY(pptx_version_id)
        REFERENCES pptx_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id) ON DELETE RESTRICT
);

CREATE INDEX idx_post_lesson_reviews_lesson
ON post_lesson_reviews(lesson_node_id, created_at DESC);
