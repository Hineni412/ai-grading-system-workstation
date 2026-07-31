CREATE TABLE material_units (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    material_version_id TEXT NOT NULL,
    unit_kind TEXT NOT NULL
        CHECK(unit_kind IN ('pdf_page', 'ppt_slide', 'image')),
    unit_index INTEGER NOT NULL
        CHECK(unit_index > 0),
    title TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    text_status TEXT NOT NULL
        CHECK(text_status IN (
            'embedded',
            'empty',
            'manual',
            'not_applicable'
        )),
    formula_review_required INTEGER NOT NULL DEFAULT 0
        CHECK(formula_review_required IN (0, 1)),
    object_summary_json TEXT NOT NULL DEFAULT '{}',
    preview_relpath TEXT NOT NULL,
    preview_sha256 TEXT NOT NULL
        CHECK(length(preview_sha256) = 64),
    source_version_sha256 TEXT NOT NULL
        CHECK(length(source_version_sha256) = 64),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(material_version_id, unit_index),
    FOREIGN KEY(material_version_id)
        REFERENCES material_versions(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_material_units_version
ON material_units(material_version_id, unit_index);

CREATE TABLE lesson_material_links (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    lesson_node_id TEXT NOT NULL,
    material_version_id TEXT NOT NULL,
    start_unit INTEGER NOT NULL
        CHECK(start_unit > 0),
    end_unit INTEGER NOT NULL
        CHECK(end_unit >= start_unit),
    crop_json TEXT,
    purpose TEXT NOT NULL
        CHECK(purpose IN (
            'textbook',
            'reference_ppt',
            'exercise',
            'answer',
            'supplement'
        )),
    teacher_note TEXT,
    confirmation_status TEXT NOT NULL
        CHECK(confirmation_status IN ('proposed', 'confirmed')),
    source_version_sha256 TEXT NOT NULL
        CHECK(length(source_version_sha256) = 64),
    sort_order INTEGER NOT NULL
        CHECK(sort_order > 0),
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
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(material_version_id)
        REFERENCES material_versions(id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_lesson_material_link_order
ON lesson_material_links(lesson_node_id, sort_order);

CREATE INDEX idx_lesson_material_links_lesson
ON lesson_material_links(lesson_node_id, is_active, sort_order);

CREATE INDEX idx_lesson_material_links_material
ON lesson_material_links(material_version_id, start_unit, end_unit);
