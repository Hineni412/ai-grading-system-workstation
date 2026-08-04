CREATE TABLE IF NOT EXISTS knowledge_graph_releases (
    release_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'knowledge-graph-release-v1'),
    taxonomy_revision INTEGER NOT NULL CHECK (taxonomy_revision >= 1),
    content_hash TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'active', 'retired', 'failed')),
    predecessor_release_id TEXT,
    source_reference TEXT NOT NULL,
    created_by TEXT NOT NULL,
    activated_by TEXT,
    activation_note TEXT,
    activated_at TEXT,
    retired_at TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (release_id LIKE 'kgr_%'),
    CHECK (length(content_hash) = 64),
    CHECK (TRIM(source_reference) <> ''),
    CHECK (TRIM(created_by) <> ''),
    CHECK (
        (status = 'active' AND activated_by IS NOT NULL AND activated_at IS NOT NULL)
        OR status <> 'active'
    ),
    FOREIGN KEY(predecessor_release_id)
        REFERENCES knowledge_graph_releases(release_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_graph_active_release
ON knowledge_graph_releases((1))
WHERE status = 'active';

CREATE TABLE IF NOT EXISTS knowledge_graph_release_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('staged', 'activated', 'retired', 'rolled_back')),
    actor_ref TEXT NOT NULL,
    reason TEXT NOT NULL,
    from_release_id TEXT,
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (TRIM(actor_ref) <> ''),
    CHECK (TRIM(reason) <> ''),
    UNIQUE(release_id, resulting_revision, event_type),
    FOREIGN KEY(release_id)
        REFERENCES knowledge_graph_releases(release_id) ON DELETE RESTRICT,
    FOREIGN KEY(from_release_id)
        REFERENCES knowledge_graph_releases(release_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS knowledge_graph_node_profiles (
    release_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    node_kind TEXT NOT NULL
        CHECK (node_kind IN ('core', 'structural', 'legacy')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'retired')),
    definition TEXT NOT NULL,
    include_scope TEXT NOT NULL,
    exclude_scope TEXT NOT NULL,
    curriculum_anchors_json TEXT NOT NULL
        CHECK (json_valid(curriculum_anchors_json)),
    observable_evidence TEXT NOT NULL,
    rationale TEXT NOT NULL,
    PRIMARY KEY(release_id, stable_key),
    CHECK (TRIM(display_name) <> ''),
    CHECK (TRIM(definition) <> ''),
    CHECK (TRIM(include_scope) <> ''),
    CHECK (TRIM(exclude_scope) <> ''),
    CHECK (TRIM(observable_evidence) <> ''),
    CHECK (TRIM(rationale) <> ''),
    FOREIGN KEY(release_id)
        REFERENCES knowledge_graph_releases(release_id) ON DELETE RESTRICT,
    FOREIGN KEY(stable_key)
        REFERENCES knowledge_tag_identities(stable_key) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS knowledge_graph_fine_term_dispositions (
    release_id TEXT NOT NULL,
    fine_term_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN (
        'direct_core', 'maps_to_core', 'maps_to_many',
        'retrieval_only', 'wrong_dimension', 'retired'
    )),
    definition TEXT NOT NULL,
    include_scope TEXT NOT NULL,
    exclude_scope TEXT NOT NULL,
    curriculum_anchors_json TEXT NOT NULL
        CHECK (json_valid(curriculum_anchors_json)),
    rationale TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    review_priority TEXT NOT NULL DEFAULT 'normal'
        CHECK (review_priority IN ('normal', 'high_impact')),
    PRIMARY KEY(release_id, fine_term_id),
    CHECK (TRIM(fine_term_id) <> ''),
    CHECK (TRIM(display_name) <> ''),
    CHECK (TRIM(definition) <> ''),
    CHECK (TRIM(include_scope) <> ''),
    CHECK (TRIM(exclude_scope) <> ''),
    CHECK (TRIM(rationale) <> ''),
    FOREIGN KEY(release_id)
        REFERENCES knowledge_graph_releases(release_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS knowledge_graph_release_mappings (
    release_id TEXT NOT NULL,
    fine_term_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    mapping_role TEXT NOT NULL
        CHECK (mapping_role IN ('primary', 'secondary', 'context_only')),
    rationale TEXT NOT NULL,
    PRIMARY KEY(release_id, fine_term_id, stable_key),
    CHECK (TRIM(rationale) <> ''),
    FOREIGN KEY(release_id, fine_term_id)
        REFERENCES knowledge_graph_fine_term_dispositions(
            release_id, fine_term_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(release_id, stable_key)
        REFERENCES knowledge_graph_node_profiles(
            release_id, stable_key
        ) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS knowledge_graph_release_relations (
    release_id TEXT NOT NULL,
    relation_key TEXT NOT NULL,
    source_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    relation_type TEXT NOT NULL
        CHECK (relation_type IN ('parent', 'prerequisite', 'related')),
    basis_kind TEXT NOT NULL CHECK (basis_kind IN (
        'mathematical_logic', 'curriculum_structure',
        'multi_textbook_sequence', 'teacher_judgment',
        'empirical_evidence'
    )),
    strength TEXT NOT NULL
        CHECK (strength IN ('required', 'recommended', 'contextual')),
    rationale TEXT NOT NULL,
    evidence_source_ids_json TEXT NOT NULL
        CHECK (json_valid(evidence_source_ids_json)),
    source_locator TEXT NOT NULL,
    PRIMARY KEY(release_id, relation_key),
    UNIQUE(release_id, source_key, target_key, relation_type),
    CHECK (length(relation_key) = 64),
    CHECK (source_key <> target_key),
    CHECK (TRIM(rationale) <> ''),
    CHECK (TRIM(source_locator) <> ''),
    FOREIGN KEY(release_id, source_key)
        REFERENCES knowledge_graph_node_profiles(
            release_id, stable_key
        ) ON DELETE RESTRICT,
    FOREIGN KEY(release_id, target_key)
        REFERENCES knowledge_graph_node_profiles(
            release_id, stable_key
        ) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS knowledge_relation_evidence (
    evidence_id TEXT PRIMARY KEY,
    relation_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    basis_kind TEXT NOT NULL CHECK (basis_kind IN (
        'mathematical_logic', 'curriculum_structure',
        'multi_textbook_sequence', 'teacher_judgment',
        'empirical_evidence'
    )),
    strength TEXT NOT NULL
        CHECK (strength IN ('required', 'recommended', 'contextual')),
    source_reference TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (length(evidence_id) = 64),
    CHECK (TRIM(source_reference) <> ''),
    CHECK (TRIM(source_locator) <> ''),
    CHECK (TRIM(rationale) <> ''),
    UNIQUE(relation_id, release_id, basis_kind, source_reference, source_locator),
    FOREIGN KEY(relation_id)
        REFERENCES knowledge_relations(relation_id) ON DELETE RESTRICT,
    FOREIGN KEY(release_id)
        REFERENCES knowledge_graph_releases(release_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS knowledge_identity_replacements (
    release_id TEXT NOT NULL,
    retired_key TEXT NOT NULL,
    replacement_key TEXT NOT NULL,
    replacement_kind TEXT NOT NULL
        CHECK (replacement_kind IN ('exact', 'broader', 'narrower', 'split')),
    rationale TEXT NOT NULL,
    PRIMARY KEY(release_id, retired_key, replacement_key),
    CHECK (retired_key <> replacement_key),
    CHECK (TRIM(rationale) <> ''),
    FOREIGN KEY(release_id)
        REFERENCES knowledge_graph_releases(release_id) ON DELETE RESTRICT,
    FOREIGN KEY(retired_key)
        REFERENCES knowledge_tag_identities(stable_key) ON DELETE RESTRICT,
    FOREIGN KEY(replacement_key)
        REFERENCES knowledge_tag_identities(stable_key) ON DELETE RESTRICT
);

ALTER TABLE fine_term_core_mappings
ADD COLUMN graph_release_id TEXT
    REFERENCES knowledge_graph_releases(release_id);

ALTER TABLE fine_term_core_mappings
ADD COLUMN mapping_role TEXT NOT NULL DEFAULT 'primary'
    CHECK (mapping_role IN ('primary', 'secondary', 'context_only'));

ALTER TABLE fine_term_core_mappings
ADD COLUMN disposition TEXT NOT NULL DEFAULT 'maps_to_core'
    CHECK (disposition IN ('direct_core', 'maps_to_core', 'maps_to_many'));

ALTER TABLE knowledge_relations
ADD COLUMN graph_release_id TEXT
    REFERENCES knowledge_graph_releases(release_id);

ALTER TABLE question_solution_evidence_versions
ADD COLUMN graph_release_id TEXT
    REFERENCES knowledge_graph_releases(release_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_graph_release_status
ON knowledge_graph_releases(status, created_at, release_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_graph_release_events
ON knowledge_graph_release_events(release_id, created_at, event_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_graph_fine_term_disposition
ON knowledge_graph_fine_term_dispositions(
    release_id, disposition, review_priority, fine_term_id
);

CREATE INDEX IF NOT EXISTS idx_knowledge_graph_release_mapping_target
ON knowledge_graph_release_mappings(release_id, stable_key, mapping_role);

CREATE INDEX IF NOT EXISTS idx_knowledge_graph_release_relation_source
ON knowledge_graph_release_relations(
    release_id, source_key, relation_type, target_key
);

CREATE INDEX IF NOT EXISTS idx_knowledge_relation_evidence_relation
ON knowledge_relation_evidence(relation_id, release_id, basis_kind);

CREATE INDEX IF NOT EXISTS idx_knowledge_identity_replacement_source
ON knowledge_identity_replacements(release_id, retired_key, replacement_kind);
