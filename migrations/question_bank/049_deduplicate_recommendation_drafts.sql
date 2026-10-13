-- migration-policy: rebuild-tables personalized_recommendation_drafts, personalized_recommendation_events
-- Keep one complete body for each revision. Abort conflicting historical bodies
-- before rebuilding either table, so the migration transaction preserves them.
CREATE TABLE personalized_recommendation_revisions (
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    draft_json TEXT NOT NULL CHECK (json_valid(draft_json)),
    PRIMARY KEY (draft_id, revision),
    FOREIGN KEY (draft_id) REFERENCES personalized_recommendation_drafts(draft_id)
        ON DELETE CASCADE
);

CREATE TRIGGER validate_recommendation_revision_migration
BEFORE INSERT ON personalized_recommendation_revisions
WHEN EXISTS (
    SELECT 1 FROM personalized_recommendation_revisions
    WHERE draft_id = NEW.draft_id AND revision = NEW.revision
      AND json(draft_json) <> json(NEW.draft_json)
)
BEGIN
    SELECT RAISE(ABORT, 'conflicting recommendation revision bodies');
END;

INSERT INTO personalized_recommendation_revisions (draft_id, revision, draft_json)
SELECT draft_id, revision, draft_json FROM personalized_recommendation_drafts;

INSERT OR IGNORE INTO personalized_recommendation_revisions (draft_id, revision, draft_json)
SELECT draft_id, resulting_revision, resulting_draft_json
FROM personalized_recommendation_events ORDER BY event_id;

INSERT OR IGNORE INTO personalized_recommendation_revisions (draft_id, revision, draft_json)
SELECT draft_id, resulting_revision, after_json
FROM personalized_recommendation_events
WHERE action = 'created' AND json_extract(after_json, '$.draft_id') = draft_id;

DROP TRIGGER validate_recommendation_revision_migration;

CREATE TABLE personalized_recommendation_drafts_deduplicated (
    draft_id TEXT PRIMARY KEY,
    request_token TEXT NOT NULL UNIQUE,
    input_fingerprint TEXT NOT NULL,
    result_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    source_version TEXT NOT NULL,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(draft_id) = 64),
    CHECK (length(request_token) = 32),
    CHECK (length(input_fingerprint) = 64),
    CHECK (length(result_version) = 64),
    CHECK (length(source_version) = 64),
    CHECK (json_valid(request_json)),
    FOREIGN KEY (draft_id, revision)
        REFERENCES personalized_recommendation_revisions(draft_id, revision)
        DEFERRABLE INITIALLY DEFERRED
);

INSERT INTO personalized_recommendation_drafts_deduplicated
SELECT draft_id, request_token, input_fingerprint, result_version, engine_version,
       source_version, request_json, status, revision, created_by, created_at, updated_at
FROM personalized_recommendation_drafts;

CREATE TABLE personalized_recommendation_events_deduplicated (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL,
    request_token TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('created', 'locked', 'unlocked', 'excluded', 'replaced')),
    actor_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(request_token) = 32),
    CHECK (length(command_hash) = 64),
    CHECK (TRIM(actor_ref) <> ''),
    CHECK (TRIM(reason) <> ''),
    CHECK (json_valid(before_json)),
    CHECK (json_valid(after_json)),
    UNIQUE (draft_id, request_token),
    FOREIGN KEY (draft_id) REFERENCES personalized_recommendation_drafts(draft_id)
        ON DELETE CASCADE,
    FOREIGN KEY (draft_id, resulting_revision)
        REFERENCES personalized_recommendation_revisions(draft_id, revision)
);

INSERT INTO personalized_recommendation_events_deduplicated
SELECT event_id, draft_id, request_token, command_hash, action, actor_ref, reason,
       expected_revision, resulting_revision, before_json,
       CASE WHEN action = 'created' AND json(after_json) = json(resulting_draft_json)
            THEN '{}' ELSE after_json END,
       created_at
FROM personalized_recommendation_events;

DROP TABLE personalized_recommendation_events;
DROP TABLE personalized_recommendation_drafts;
ALTER TABLE personalized_recommendation_drafts_deduplicated RENAME TO personalized_recommendation_drafts;
ALTER TABLE personalized_recommendation_events_deduplicated RENAME TO personalized_recommendation_events;

CREATE INDEX idx_personalized_draft_status
ON personalized_recommendation_drafts(status, updated_at, draft_id);
CREATE INDEX idx_personalized_event_draft
ON personalized_recommendation_events(draft_id, resulting_revision);

CREATE TRIGGER recommendation_revision_immutable
BEFORE UPDATE ON personalized_recommendation_revisions
BEGIN
    SELECT RAISE(ABORT, 'recommendation revisions are immutable');
END;

CREATE TRIGGER dv_personalized_recommendation_drafts_ins AFTER INSERT ON personalized_recommendation_drafts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_drafts_upd AFTER UPDATE ON personalized_recommendation_drafts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_drafts_del AFTER DELETE ON personalized_recommendation_drafts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_events_ins AFTER INSERT ON personalized_recommendation_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_events_upd AFTER UPDATE ON personalized_recommendation_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_events_del AFTER DELETE ON personalized_recommendation_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_revisions_ins AFTER INSERT ON personalized_recommendation_revisions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_revisions_upd AFTER UPDATE ON personalized_recommendation_revisions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
CREATE TRIGGER dv_personalized_recommendation_revisions_del AFTER DELETE ON personalized_recommendation_revisions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;
