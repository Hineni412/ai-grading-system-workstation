-- Global teaching-term scope: persist one explicit curriculum volume per exam.
ALTER TABLE grading_sessions
ADD COLUMN curriculum_volume_id TEXT
CHECK (
    curriculum_volume_id IS NULL
    OR (
        LENGTH(TRIM(curriculum_volume_id)) BETWEEN 1 AND 80
        AND curriculum_volume_id NOT GLOB '*[^A-Za-z0-9_-]*'
    )
);

CREATE INDEX IF NOT EXISTS idx_grading_sessions_curriculum_volume
ON grading_sessions(curriculum_volume_id, is_deleted, updated_at DESC);
