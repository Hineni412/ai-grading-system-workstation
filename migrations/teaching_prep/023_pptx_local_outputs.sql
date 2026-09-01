-- 本地执行产物：python-pptx 在隔离副本上生成并经机械审计的成片。
CREATE TABLE pptx_local_outputs (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    lesson_node_id TEXT NOT NULL,
    slide_plan_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    resource_pack_id TEXT NOT NULL,
    version_number INTEGER NOT NULL
        CHECK(version_number > 0),
    status TEXT NOT NULL
        CHECK(status IN ('ready', 'superseded')),
    output_relpath TEXT NOT NULL UNIQUE,
    output_filename TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    source_material_version_id TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    source_page_count INTEGER NOT NULL
        CHECK(source_page_count > 0),
    final_page_count INTEGER NOT NULL
        CHECK(final_page_count > 0),
    lesson_kind TEXT NOT NULL,
    execution_report_json TEXT NOT NULL,
    audit_report_json TEXT NOT NULL,
    inserted_question_pages_json TEXT NOT NULL,
    worksheet_relpath TEXT,
    worksheet_filename TEXT,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(lesson_node_id, version_number),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(slide_plan_id)
        REFERENCES slide_plan_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(draft_id)
        REFERENCES lesson_draft_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(resource_pack_id)
        REFERENCES resource_pack_versions(id)
        ON DELETE RESTRICT
);
