-- migration-policy: rebuild-tables knowledge_relations
-- Keep historical relation rows, but make uniqueness and active-pair safety
-- local to the immutable graph release that owns each relation.

DROP VIEW IF EXISTS knowledge_active_relations;

CREATE TABLE knowledge_relations_rebuild (
    relation_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    relation_type TEXT NOT NULL
        CHECK (relation_type IN ('parent', 'prerequisite', 'related')),
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested', 'confirmed', 'rejected', 'retired')),
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('system', 'model', 'teacher', 'import')),
    source_reference TEXT,
    source_operation_id TEXT,
    rationale TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    decision_by TEXT,
    decision_note TEXT,
    decided_at TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    prompt_version TEXT,
    confidence REAL
        CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    conflict_codes_json TEXT NOT NULL DEFAULT '[]',
    graph_release_id TEXT,
    CHECK (TRIM(relation_id) <> ''),
    CHECK (source_key <> target_key),
    CHECK (TRIM(rationale) <> ''),
    CHECK (relation_type <> 'related' OR source_key < target_key),
    CHECK (
        source_kind <> 'model'
        OR (
            model_name IS NOT NULL
            AND TRIM(model_name) <> ''
            AND model_version IS NOT NULL
            AND TRIM(model_version) <> ''
        )
    ),
    CHECK (
        (status = 'suggested' AND decision_by IS NULL AND decided_at IS NULL)
        OR (
            status <> 'suggested'
            AND decision_by IS NOT NULL
            AND TRIM(decision_by) <> ''
            AND decided_at IS NOT NULL
        )
    ),
    UNIQUE(graph_release_id, source_key, target_key, relation_type),
    FOREIGN KEY(source_key) REFERENCES knowledge_tag_identities(stable_key),
    FOREIGN KEY(target_key) REFERENCES knowledge_tag_identities(stable_key),
    FOREIGN KEY(graph_release_id)
        REFERENCES knowledge_graph_releases(release_id)
);

INSERT INTO knowledge_relations_rebuild (
    relation_id,
    source_key,
    target_key,
    relation_type,
    status,
    source_kind,
    source_reference,
    source_operation_id,
    rationale,
    model_name,
    model_version,
    decision_by,
    decision_note,
    decided_at,
    revision,
    created_at,
    updated_at,
    prompt_version,
    confidence,
    conflict_codes_json,
    graph_release_id
)
SELECT
    relation_id,
    source_key,
    target_key,
    relation_type,
    status,
    source_kind,
    source_reference,
    source_operation_id,
    rationale,
    model_name,
    model_version,
    decision_by,
    decision_note,
    decided_at,
    revision,
    created_at,
    updated_at,
    prompt_version,
    confidence,
    conflict_codes_json,
    graph_release_id
FROM knowledge_relations;

DROP TABLE knowledge_relations;
ALTER TABLE knowledge_relations_rebuild RENAME TO knowledge_relations;

CREATE INDEX idx_knowledge_relations_review
ON knowledge_relations(status, relation_type, updated_at, relation_id);

CREATE INDEX idx_knowledge_relations_source
ON knowledge_relations(
    graph_release_id, source_key, status, relation_type, target_key
);

CREATE INDEX idx_knowledge_relations_target
ON knowledge_relations(
    graph_release_id, target_key, status, relation_type, source_key
);

CREATE UNIQUE INDEX uq_knowledge_active_pair
ON knowledge_relations(
    graph_release_id,
    CASE WHEN source_key < target_key THEN source_key ELSE target_key END,
    CASE WHEN source_key < target_key THEN target_key ELSE source_key END
)
WHERE status = 'confirmed' AND graph_release_id IS NOT NULL;

CREATE VIEW knowledge_active_relations AS
SELECT
    relation.relation_id,
    relation.source_key,
    source_identity.display_name AS source_name,
    relation.target_key,
    target_identity.display_name AS target_name,
    relation.relation_type,
    relation.rationale,
    relation.revision,
    relation.updated_at
FROM knowledge_relations relation
JOIN knowledge_tag_identities source_identity
  ON source_identity.stable_key = relation.source_key
JOIN knowledge_tag_identities target_identity
  ON target_identity.stable_key = relation.target_key
JOIN knowledge_graph_releases current_release
  ON current_release.release_id = relation.graph_release_id
 AND current_release.status = 'active'
WHERE relation.status = 'confirmed'
  AND source_identity.status = 'active'
  AND target_identity.status = 'active';
