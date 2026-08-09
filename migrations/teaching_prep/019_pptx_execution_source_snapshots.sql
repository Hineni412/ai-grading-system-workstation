-- migration-policy: rebuild-tables pptx_execution_runs

CREATE TABLE pptx_execution_source_snapshots (
    material_version_id TEXT PRIMARY KEY
        CHECK(length(material_version_id) = 32),
    source_id TEXT NOT NULL
        CHECK(length(source_id) = 32),
    display_name TEXT NOT NULL
        CHECK(length(trim(display_name)) BETWEEN 1 AND 200),
    file_name TEXT NOT NULL
        CHECK(length(trim(file_name)) BETWEEN 1 AND 240),
    material_type TEXT NOT NULL
        CHECK(material_type IN ('pdf', 'pptx', 'image')),
    content_sha256 TEXT NOT NULL
        CHECK(length(content_sha256) = 64),
    size_bytes INTEGER NOT NULL
        CHECK(size_bytes >= 0),
    schema_version INTEGER NOT NULL DEFAULT 1
        CHECK(schema_version = 1),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

INSERT INTO pptx_execution_source_snapshots (
    material_version_id,
    source_id,
    display_name,
    file_name,
    material_type,
    content_sha256,
    size_bytes,
    schema_version,
    created_at
)
SELECT
    version.id,
    version.source_id,
    source.display_name,
    version.file_name,
    source.material_type,
    version.content_sha256,
    version.size_bytes,
    1,
    MIN(run.created_at)
FROM pptx_execution_runs AS run
JOIN material_versions AS version
  ON version.id = run.source_material_version_id
JOIN material_sources AS source
  ON source.id = version.source_id
GROUP BY
    version.id,
    version.source_id,
    source.display_name,
    version.file_name,
    source.material_type,
    version.content_sha256,
    version.size_bytes;

CREATE TABLE pptx_execution_runs_new (
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
    wps_started_at TEXT,
    wps_invocation_count INTEGER NOT NULL DEFAULT 0
        CHECK(wps_invocation_count IN (0, 1)),
    phase TEXT NOT NULL DEFAULT 'copying'
        CHECK(phase IN (
            'copying',
            'executing',
            'verifying',
            'publishing',
            'done'
        )),
    phase_started_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
        CHECK(cancel_requested IN (0, 1)),
    FOREIGN KEY(operation_id)
        REFERENCES teaching_prep_operations(operation_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(slide_plan_id)
        REFERENCES slide_plan_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(source_material_version_id)
        REFERENCES pptx_execution_source_snapshots(material_version_id)
        ON DELETE RESTRICT
);

INSERT INTO pptx_execution_runs_new (
    id,
    operation_id,
    request_hash,
    slide_plan_id,
    source_material_version_id,
    source_sha256,
    staging_name,
    expected_slide_count,
    status,
    execution_report_json,
    verification_report_json,
    error_code,
    published_version_id,
    created_at,
    updated_at,
    finished_at,
    wps_started_at,
    wps_invocation_count,
    phase,
    phase_started_at,
    cancel_requested
)
SELECT
    id,
    operation_id,
    request_hash,
    slide_plan_id,
    source_material_version_id,
    source_sha256,
    staging_name,
    expected_slide_count,
    status,
    execution_report_json,
    verification_report_json,
    error_code,
    published_version_id,
    created_at,
    updated_at,
    finished_at,
    wps_started_at,
    wps_invocation_count,
    phase,
    phase_started_at,
    cancel_requested
FROM pptx_execution_runs;

DROP TABLE pptx_execution_runs;

ALTER TABLE pptx_execution_runs_new
RENAME TO pptx_execution_runs;

CREATE INDEX idx_pptx_execution_runs_status
ON pptx_execution_runs(status, updated_at);
