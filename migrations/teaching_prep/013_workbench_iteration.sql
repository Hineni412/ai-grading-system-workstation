CREATE TABLE reference_selection_drafts (
    lesson_node_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    source_state_sha256 TEXT NOT NULL
        CHECK(length(source_state_sha256) = 64),
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
        ON DELETE CASCADE
);

CREATE TABLE reference_selection_snapshots (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    lesson_node_id TEXT NOT NULL,
    request_token TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    source_state_sha256 TEXT NOT NULL
        CHECK(length(source_state_sha256) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_reference_selection_snapshots_lesson
ON reference_selection_snapshots(lesson_node_id, created_at DESC);

CREATE TABLE exercise_suggestion_runs (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    snapshot_id TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN (
            'running', 'succeeded', 'failed', 'cancelled', 'result_unknown'
        )),
    error_code TEXT,
    model_call_count INTEGER NOT NULL DEFAULT 0
        CHECK(model_call_count IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    finished_at TEXT,
    FOREIGN KEY(snapshot_id)
        REFERENCES reference_selection_snapshots(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_exercise_suggestion_runs_snapshot
ON exercise_suggestion_runs(snapshot_id, created_at DESC);

CREATE TABLE exercise_suggestions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    run_id TEXT NOT NULL,
    lesson_node_id TEXT NOT NULL,
    source_state_sha256 TEXT NOT NULL
        CHECK(length(source_state_sha256) = 64),
    decision TEXT NOT NULL DEFAULT 'pending'
        CHECK(decision IN ('pending', 'accepted', 'modified', 'rejected')),
    original_payload_json TEXT NOT NULL,
    teacher_payload_json TEXT,
    rejection_reason TEXT,
    exercise_candidate_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(run_id)
        REFERENCES exercise_suggestion_runs(id)
        ON DELETE CASCADE,
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE CASCADE,
    FOREIGN KEY(exercise_candidate_id)
        REFERENCES exercise_candidates(id)
        ON DELETE SET NULL
);

CREATE INDEX idx_exercise_suggestions_run
ON exercise_suggestions(run_id, created_at, id);

CREATE TABLE lesson_current_pptx_versions (
    lesson_node_id TEXT PRIMARY KEY,
    pptx_version_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE CASCADE,
    FOREIGN KEY(pptx_version_id)
        REFERENCES pptx_versions(id)
        ON DELETE RESTRICT
);

-- Existing trusted versions remain visible after the pointer table is added.
-- Pick the newest published version without rewriting or deleting any file.
INSERT INTO lesson_current_pptx_versions (
    lesson_node_id,
    pptx_version_id,
    revision
)
SELECT candidate.lesson_node_id, candidate.id, 1
FROM pptx_versions AS candidate
WHERE candidate.status = 'published'
  AND NOT EXISTS (
      SELECT 1
      FROM pptx_versions AS newer
      WHERE newer.lesson_node_id = candidate.lesson_node_id
        AND newer.status = 'published'
        AND newer.version_number > candidate.version_number
  );

CREATE TABLE pptx_version_activations (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    lesson_node_id TEXT NOT NULL,
    pptx_version_id TEXT NOT NULL,
    previous_pptx_version_id TEXT,
    resulting_revision INTEGER NOT NULL
        CHECK(resulting_revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE CASCADE,
    FOREIGN KEY(pptx_version_id)
        REFERENCES pptx_versions(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(previous_pptx_version_id)
        REFERENCES pptx_versions(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_pptx_version_activations_lesson
ON pptx_version_activations(lesson_node_id, created_at DESC);

ALTER TABLE pptx_execution_runs
ADD COLUMN phase TEXT NOT NULL DEFAULT 'copying'
    CHECK(phase IN ('copying', 'executing', 'verifying', 'publishing', 'done'));

ALTER TABLE pptx_execution_runs
ADD COLUMN phase_started_at TEXT;

ALTER TABLE pptx_execution_runs
ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0
    CHECK(cancel_requested IN (0, 1));
