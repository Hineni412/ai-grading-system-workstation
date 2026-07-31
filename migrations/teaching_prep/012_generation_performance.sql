ALTER TABLE pptx_execution_runs
ADD COLUMN wps_started_at TEXT;

ALTER TABLE pptx_execution_runs
ADD COLUMN wps_invocation_count INTEGER NOT NULL DEFAULT 0
    CHECK(wps_invocation_count IN (0, 1));
