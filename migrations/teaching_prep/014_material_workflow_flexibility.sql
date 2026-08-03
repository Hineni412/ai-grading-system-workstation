ALTER TABLE material_sources
ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0);

ALTER TABLE material_sources
ADD COLUMN archived_at TEXT;

ALTER TABLE material_sources
ADD COLUMN updated_at TEXT;

UPDATE material_sources
SET updated_at = created_at
WHERE updated_at IS NULL;

ALTER TABLE material_versions
ADD COLUMN parse_expected_unit_count INTEGER
    CHECK(parse_expected_unit_count IS NULL OR parse_expected_unit_count >= 0);

UPDATE material_units
SET object_summary_json = json_set(
    object_summary_json,
    '$.ocr_required',
    CASE
        WHEN unit_kind = 'pdf_page'
         AND text_status <> 'manual'
         AND trim(extracted_text) = ''
         AND json_extract(object_summary_json, '$.has_page_images') = 1
        THEN 1 ELSE 0
    END
);

ALTER TABLE semester_material_records
ADD COLUMN is_daily_workbook INTEGER NOT NULL DEFAULT 0
    CHECK(is_daily_workbook IN (0, 1));

ALTER TABLE semester_material_records
ADD COLUMN workbook_series TEXT;

ALTER TABLE semester_material_records
ADD COLUMN workbook_volume TEXT
    CHECK(workbook_volume IS NULL OR workbook_volume IN ('A', 'B'));

UPDATE semester_material_records
SET is_daily_workbook = 1
WHERE material_role = 'homework_workbook';

CREATE UNIQUE INDEX uq_semester_daily_workbook_volume
ON semester_material_records(
    semester_id,
    lower(workbook_series),
    workbook_volume
)
WHERE is_daily_workbook = 1
  AND is_active = 1
  AND workbook_series IS NOT NULL
  AND workbook_volume IS NOT NULL;

CREATE INDEX idx_material_sources_archived
ON material_sources(archived_at, updated_at);
