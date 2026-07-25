-- migration-policy: drop-tables knowledge_concepts, knowledge_relations, knowledge_source_mappings, skill_topics, skills, assessment_item_skills, question_skill_links, skill_resolution_conflicts, skill_neighbors, skill_system_settings, skill_migration_runs
-- P3-17: retire the replaced knowledge-alignment and unified-skill structures.

CREATE TEMP TABLE p3_17_schema_guard (
    token INTEGER NOT NULL UNIQUE
);

INSERT INTO p3_17_schema_guard (token) VALUES (1);

INSERT INTO p3_17_schema_guard (token)
SELECT 1
WHERE
    (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('knowledge_concepts') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:canonical_key:TEXT:1:0|2:name:TEXT:1:0|3:aliases_json:TEXT:1:0|4:subject:TEXT:0:0|5:grade:TEXT:0:0|6:status:TEXT:1:0|7:created_at:TEXT:1:0|8:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('knowledge_relations') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:source_concept_id:INTEGER:1:0|2:target_concept_id:INTEGER:1:0|3:relation_type:TEXT:1:0|4:weight:REAL:1:0|5:metadata_json:TEXT:1:0|6:created_at:TEXT:1:0|7:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('knowledge_source_mappings') ORDER BY cid
    )) NOT IN (
        '0:id:INTEGER:0:1|1:source_namespace:TEXT:1:0|2:source_value:TEXT:1:0|3:normalized_value:TEXT:1:0|4:concept_id:INTEGER:0:0|5:status:TEXT:1:0|6:confidence:REAL:1:0|7:sub_skill_tags:TEXT:0:0|8:evidence_json:TEXT:1:0|9:reviewed_by:TEXT:0:0|10:reviewed_at:TEXT:0:0|11:created_at:TEXT:1:0|12:updated_at:TEXT:1:0',
        '0:id:INTEGER:0:1|1:source_namespace:TEXT:1:0|2:source_value:TEXT:1:0|3:normalized_value:TEXT:1:0|4:concept_id:INTEGER:0:0|5:status:TEXT:1:0|6:confidence:REAL:1:0|7:evidence_json:TEXT:1:0|8:reviewed_by:TEXT:0:0|9:reviewed_at:TEXT:0:0|10:created_at:TEXT:1:0|11:updated_at:TEXT:1:0|12:sub_skill_tags:TEXT:0:0'
    )
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('skill_topics') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:stable_key:TEXT:1:0|2:name:TEXT:1:0|3:subject:TEXT:1:0|4:grade_min:INTEGER:0:0|5:grade_max:INTEGER:0:0|6:status:TEXT:1:0|7:created_at:TEXT:1:0|8:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('skills') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:stable_key:TEXT:1:0|2:topic_id:INTEGER:1:0|3:name:TEXT:1:0|4:aliases_json:TEXT:1:0|5:grade_min:INTEGER:0:0|6:grade_max:INTEGER:0:0|7:origin:TEXT:1:0|8:status:TEXT:1:0|9:redirect_skill_id:INTEGER:0:0|10:created_at:TEXT:1:0|11:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('assessment_item_skills') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:grading_session_id:TEXT:1:0|2:source_question_id:TEXT:1:0|3:skill_id:INTEGER:0:0|4:role:TEXT:1:0|5:raw_knowledge_id:TEXT:0:0|6:raw_knowledge_label:TEXT:0:0|7:source:TEXT:1:0|8:confidence:REAL:1:0|9:evidence_json:TEXT:1:0|10:status:TEXT:1:0|11:created_at:TEXT:1:0|12:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('question_skill_links') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:question_id:INTEGER:1:0|2:skill_id:INTEGER:0:0|3:role:TEXT:1:0|4:raw_knowledge_id:TEXT:0:0|5:raw_knowledge_label:TEXT:0:0|6:source:TEXT:1:0|7:confidence:REAL:1:0|8:evidence_json:TEXT:1:0|9:status:TEXT:1:0|10:created_at:TEXT:1:0|11:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('skill_resolution_conflicts') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:source_type:TEXT:1:0|2:source_ref:TEXT:1:0|3:raw_label:TEXT:1:0|4:normalized_label:TEXT:1:0|5:candidate_skill_ids_json:TEXT:1:0|6:reason:TEXT:1:0|7:evidence_json:TEXT:1:0|8:state:TEXT:1:0|9:resolved_skill_id:INTEGER:0:0|10:resolved_by:TEXT:0:0|11:resolved_at:TEXT:0:0|12:created_at:TEXT:1:0|13:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('skill_neighbors') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:source_skill_id:INTEGER:1:0|2:target_skill_id:INTEGER:1:0|3:kind:TEXT:1:0|4:weight:REAL:1:0|5:source:TEXT:1:0|6:enabled:INTEGER:1:0|7:evidence_json:TEXT:1:0|8:created_at:TEXT:1:0|9:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('skill_system_settings') ORDER BY cid
    )) <> '0:key:TEXT:0:1|1:value:TEXT:1:0|2:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%s:%s:%d:%d', cid, name, type, "notnull", pk) AS signature
        FROM pragma_table_info('skill_migration_runs') ORDER BY cid
    )) <> '0:id:INTEGER:0:1|1:batch_id:TEXT:1:0|2:mode:TEXT:1:0|3:status:TEXT:1:0|4:source_counts_json:TEXT:1:0|5:result_counts_json:TEXT:1:0|6:invariants_json:TEXT:1:0|7:report_path:TEXT:0:0|8:backup_json:TEXT:1:0|9:error_message:TEXT:0:0|10:started_at:TEXT:1:0|11:finished_at:TEXT:0:0|12:created_at:TEXT:1:0|13:updated_at:TEXT:1:0'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%d:%s:%s:%s:%s:%s:%s', id, seq, "table", "from", "to", on_update, on_delete, match) AS signature
        FROM pragma_foreign_key_list('knowledge_relations') ORDER BY id, seq
    )) <> '0:0:knowledge_concepts:target_concept_id:id:NO ACTION:NO ACTION:NONE|1:0:knowledge_concepts:source_concept_id:id:NO ACTION:NO ACTION:NONE'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%d:%s:%s:%s:%s:%s:%s', id, seq, "table", "from", "to", on_update, on_delete, match) AS signature
        FROM pragma_foreign_key_list('knowledge_source_mappings') ORDER BY id, seq
    )) <> '0:0:knowledge_concepts:concept_id:id:NO ACTION:NO ACTION:NONE'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%d:%s:%s:%s:%s:%s:%s', id, seq, "table", "from", "to", on_update, on_delete, match) AS signature
        FROM pragma_foreign_key_list('skills') ORDER BY id, seq
    )) <> '0:0:skills:redirect_skill_id:id:NO ACTION:NO ACTION:NONE|1:0:skill_topics:topic_id:id:NO ACTION:NO ACTION:NONE'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%d:%s:%s:%s:%s:%s:%s', id, seq, "table", "from", "to", on_update, on_delete, match) AS signature
        FROM pragma_foreign_key_list('assessment_item_skills') ORDER BY id, seq
    )) <> '0:0:skills:skill_id:id:NO ACTION:NO ACTION:NONE'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%d:%s:%s:%s:%s:%s:%s', id, seq, "table", "from", "to", on_update, on_delete, match) AS signature
        FROM pragma_foreign_key_list('question_skill_links') ORDER BY id, seq
    )) <> '0:0:skills:skill_id:id:NO ACTION:NO ACTION:NONE|1:0:questions:question_id:id:NO ACTION:NO ACTION:NONE'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%d:%s:%s:%s:%s:%s:%s', id, seq, "table", "from", "to", on_update, on_delete, match) AS signature
        FROM pragma_foreign_key_list('skill_resolution_conflicts') ORDER BY id, seq
    )) <> '0:0:skills:resolved_skill_id:id:NO ACTION:NO ACTION:NONE'
    OR (SELECT group_concat(signature, '|') FROM (
        SELECT printf('%d:%d:%s:%s:%s:%s:%s:%s', id, seq, "table", "from", "to", on_update, on_delete, match) AS signature
        FROM pragma_foreign_key_list('skill_neighbors') ORDER BY id, seq
    )) <> '0:0:skills:target_skill_id:id:NO ACTION:NO ACTION:NONE|1:0:skills:source_skill_id:id:NO ACTION:NO ACTION:NONE'
LIMIT 1;

DROP TABLE skill_neighbors;
DROP TABLE skill_resolution_conflicts;
DROP TABLE question_skill_links;
DROP TABLE assessment_item_skills;
DROP TABLE skills;
DROP TABLE skill_topics;
DROP TABLE knowledge_relations;
DROP TABLE knowledge_source_mappings;
DROP TABLE knowledge_concepts;
DROP TABLE skill_system_settings;
DROP TABLE skill_migration_runs;
