-- migration-policy: rebuild-tables knowledge_tag_identities, knowledge_graph_node_profiles
-- 技能层入知识标准：knowledge_tag_identities 放行 sk_* 稳定键，
-- knowledge_graph_node_profiles 放行 node_kind='skill'。行数据原样保留。
-- knowledge_active_relations 视图依赖 knowledge_tag_identities，重建期间
-- 需要 legacy_alter_table 跳过 RENAME 的依赖对象重写校验。

PRAGMA legacy_alter_table = ON;

CREATE TABLE knowledge_tag_identities_v2 (
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
        OR stable_key LIKE 'sk_%'
    ),
    CHECK (TRIM(display_name) <> ''),
    CHECK (
        (status = 'active' AND retired_at IS NULL)
        OR (status = 'retired' AND retired_at IS NOT NULL)
    )
);

INSERT INTO knowledge_tag_identities_v2 (
    stable_key,
    display_name,
    origin,
    status,
    revision,
    created_at,
    updated_at,
    retired_at
)
SELECT
    stable_key,
    display_name,
    origin,
    status,
    revision,
    created_at,
    updated_at,
    retired_at
FROM knowledge_tag_identities;

DROP TABLE knowledge_tag_identities;

ALTER TABLE knowledge_tag_identities_v2
RENAME TO knowledge_tag_identities;

CREATE TABLE knowledge_graph_node_profiles_v2 (
    release_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    node_kind TEXT NOT NULL
        CHECK (node_kind IN ('core', 'structural', 'legacy', 'skill')),
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

INSERT INTO knowledge_graph_node_profiles_v2 (
    release_id,
    stable_key,
    display_name,
    node_kind,
    status,
    definition,
    include_scope,
    exclude_scope,
    curriculum_anchors_json,
    observable_evidence,
    rationale
)
SELECT
    release_id,
    stable_key,
    display_name,
    node_kind,
    status,
    definition,
    include_scope,
    exclude_scope,
    curriculum_anchors_json,
    observable_evidence,
    rationale
FROM knowledge_graph_node_profiles;

DROP TABLE knowledge_graph_node_profiles;

ALTER TABLE knowledge_graph_node_profiles_v2
RENAME TO knowledge_graph_node_profiles;

PRAGMA legacy_alter_table = OFF;
