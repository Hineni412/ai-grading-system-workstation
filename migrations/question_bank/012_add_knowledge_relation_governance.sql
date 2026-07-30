CREATE TABLE IF NOT EXISTS knowledge_tag_identities (
    stable_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('builtin', 'local')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'retired')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    retired_at TEXT,
    CHECK (TRIM(stable_key) = stable_key AND stable_key = LOWER(stable_key)),
    CHECK (
        stable_key LIKE 'kp_%'
        OR stable_key LIKE 'ki_%'
    ),
    CHECK (TRIM(display_name) <> ''),
    CHECK (
        (status = 'active' AND retired_at IS NULL)
        OR (status = 'retired' AND retired_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS knowledge_tag_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_kind TEXT NOT NULL
        CHECK (alias_kind IN ('stable_key', 'canonical_name', 'registered_alias')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(alias) <> ''),
    CHECK (TRIM(normalized_alias) <> ''),
    UNIQUE(stable_key, normalized_alias),
    FOREIGN KEY(stable_key) REFERENCES knowledge_tag_identities(stable_key)
);

CREATE TABLE IF NOT EXISTS knowledge_tag_identity_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_tag_id INTEGER NOT NULL UNIQUE,
    stable_key TEXT NOT NULL,
    source_value_snapshot TEXT NOT NULL,
    mapping_source TEXT NOT NULL DEFAULT 'governed_exact'
        CHECK (mapping_source IN ('governed_exact', 'teacher')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(source_value_snapshot) <> ''),
    FOREIGN KEY(question_tag_id) REFERENCES question_tags(id) ON DELETE CASCADE,
    FOREIGN KEY(stable_key) REFERENCES knowledge_tag_identities(stable_key)
);

CREATE TABLE IF NOT EXISTS knowledge_relations (
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
    CHECK (TRIM(relation_id) <> ''),
    CHECK (source_key <> target_key),
    CHECK (TRIM(rationale) <> ''),
    CHECK (
        relation_type <> 'related'
        OR source_key < target_key
    ),
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
    UNIQUE(source_key, target_key, relation_type),
    FOREIGN KEY(source_key) REFERENCES knowledge_tag_identities(stable_key),
    FOREIGN KEY(target_key) REFERENCES knowledge_tag_identities(stable_key)
);

CREATE TABLE IF NOT EXISTS knowledge_relation_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('suggested', 'confirmed', 'rejected', 'retired', 'restored')),
    from_status TEXT
        CHECK (
            from_status IS NULL
            OR from_status IN ('suggested', 'confirmed', 'rejected', 'retired')
        ),
    to_status TEXT NOT NULL
        CHECK (to_status IN ('suggested', 'confirmed', 'rejected', 'retired')),
    actor_kind TEXT NOT NULL
        CHECK (actor_kind IN ('system', 'model', 'teacher', 'import')),
    actor_ref TEXT,
    reason TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    source_operation_id TEXT,
    model_name TEXT,
    model_version TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(reason) <> ''),
    UNIQUE(relation_id, resulting_revision),
    FOREIGN KEY(relation_id) REFERENCES knowledge_relations(relation_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_alias_lookup
ON knowledge_tag_aliases(normalized_alias, stable_key);

CREATE INDEX IF NOT EXISTS idx_knowledge_mapping_identity
ON knowledge_tag_identity_mappings(stable_key, question_tag_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_review
ON knowledge_relations(status, relation_type, updated_at, relation_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
ON knowledge_relations(source_key, status, relation_type, target_key);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
ON knowledge_relations(target_key, status, relation_type, source_key);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_active_pair
ON knowledge_relations(
    CASE WHEN source_key < target_key THEN source_key ELSE target_key END,
    CASE WHEN source_key < target_key THEN target_key ELSE source_key END
)
WHERE status = 'confirmed';

CREATE INDEX IF NOT EXISTS idx_knowledge_relation_audit
ON knowledge_relation_audit_events(relation_id, resulting_revision);

CREATE VIEW IF NOT EXISTS knowledge_active_relations AS
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
WHERE relation.status = 'confirmed'
  AND source_identity.status = 'active'
  AND target_identity.status = 'active';
