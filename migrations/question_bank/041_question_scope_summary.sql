-- question_scope_summary: precomputed per-question scope summary used by the
-- strict/primary/any scope_mode filter (redesign §8). Rows are refreshed by
-- refresh_question_scope_summary() whenever a question's current usable
-- evidence version or its §4.1 links change; a missing row means the question
-- has no usable evidence version and callers fall back to tag matching.
CREATE TABLE IF NOT EXISTS question_scope_summary (
    question_id                 INTEGER PRIMARY KEY
        REFERENCES questions(id),
    evidence_version_id         TEXT NOT NULL DEFAULT '',
    graph_release_id            TEXT NOT NULL DEFAULT '',
    primary_section_id          TEXT NOT NULL DEFAULT '',
    direct_section_ids_json     TEXT NOT NULL DEFAULT '[]',
    supporting_max_volume_order INTEGER NOT NULL DEFAULT 0,
    has_cross_chapter_direct    INTEGER NOT NULL DEFAULT 0,
    updated_at                  TEXT NOT NULL
        DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_question_scope_summary_primary
    ON question_scope_summary(primary_section_id);
