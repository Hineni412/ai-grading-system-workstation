-- evidence_point_knowledge_links: links between frozen evidence points and
-- governed knowledge terms (skills / section nodes), kept outside the
-- immutable evidence JSON so reads do not depend on embedded fine_term_links.
CREATE TABLE IF NOT EXISTS evidence_point_knowledge_links (
    evidence_version_id TEXT NOT NULL
        REFERENCES question_solution_evidence_versions(evidence_version_id),
    question_id         INTEGER NOT NULL
        REFERENCES questions(id),
    part_id             TEXT NOT NULL,
    evidence_point_id   TEXT NOT NULL,
    graph_release_id    TEXT NOT NULL
        REFERENCES knowledge_graph_releases(release_id),
    role                TEXT NOT NULL
        CHECK (role IN ('direct', 'supporting_prerequisite')),
    term_id             TEXT NOT NULL,
    stable_key          TEXT,
    resolution_status   TEXT NOT NULL
        CHECK (resolution_status IN ('resolved', 'unresolved')),
    weight              REAL NOT NULL DEFAULT 1.0,
    source_kind         TEXT NOT NULL
        CHECK (source_kind IN ('link_job', 'migrated_from_embedded', 'teacher')),
    source_reference    TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (
        evidence_version_id, evidence_point_id,
        graph_release_id, role, term_id
    )
);

CREATE INDEX IF NOT EXISTS idx_epkl_question
    ON evidence_point_knowledge_links (question_id, graph_release_id);

CREATE INDEX IF NOT EXISTS idx_epkl_stable_key
    ON evidence_point_knowledge_links (stable_key, graph_release_id);

-- One-time copy of resolved embedded fine_term_links from each question's
-- latest usable evidence version. The immutable evidence JSON is untouched.
-- The owning release is parsed from core_resolution.reason
-- ("current_release:<release_id>"); rows whose release cannot be resolved to
-- an installed release fall back to the version's own release, and are only
-- inserted when a matching release row exists (FK-safe).
INSERT OR IGNORE INTO evidence_point_knowledge_links (
    evidence_version_id, question_id, part_id, evidence_point_id,
    graph_release_id, role, term_id, stable_key, resolution_status,
    weight, source_kind, source_reference
)
SELECT
    v.evidence_version_id,
    v.question_id,
    json_extract(p.value, '$.part_id'),
    json_extract(e.value, '$.evidence_point_id'),
    r.release_id,
    json_extract(l.value, '$.role'),
    json_extract(l.value, '$.fine_term_id'),
    json_extract(l.value, '$.core_resolution.stable_keys[0]'),
    'resolved',
    CASE
        WHEN json_extract(l.value, '$.role') = 'direct' THEN
            1.0 / (
                SELECT COUNT(*)
                FROM json_each(e.value, '$.fine_term_links') l2
                WHERE json_extract(l2.value, '$.role') = 'direct'
                  AND json_extract(l2.value, '$.core_resolution.status') = 'resolved'
            )
        ELSE 1.0
    END,
    'migrated_from_embedded',
    'migration:040'
FROM question_solution_evidence_versions v
JOIN (
    SELECT question_id, MAX(created_at) AS max_created
    FROM question_solution_evidence_versions
    WHERE status IN ('proposed', 'approved')
    GROUP BY question_id
) m
    ON m.question_id = v.question_id
   AND m.max_created = v.created_at
, json_each(v.evidence_json, '$.parts') p
, json_each(p.value, '$.evidence_points') e
, json_each(e.value, '$.fine_term_links') l
JOIN knowledge_graph_releases r
    ON r.release_id = COALESCE(
        NULLIF(
            CASE
                WHEN substr(
                    json_extract(l.value, '$.core_resolution.reason'),
                    1, length('current_release:')
                ) = 'current_release:'
                THEN substr(
                    json_extract(l.value, '$.core_resolution.reason'),
                    length('current_release:') + 1
                )
            END,
            ''
        ),
        v.graph_release_id
    )
WHERE v.status IN ('proposed', 'approved')
  AND json_extract(l.value, '$.role') IN ('direct', 'supporting_prerequisite')
  AND json_extract(l.value, '$.core_resolution.status') = 'resolved'
  AND json_extract(l.value, '$.core_resolution.stable_keys[0]') IS NOT NULL;
