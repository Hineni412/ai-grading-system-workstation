-- migration-policy: drop-tables grading_details, exam_results
-- P3-16: retire the tables used only by the removed main.py workflow.

CREATE TEMP TABLE p3_16_schema_guard (
    token INTEGER NOT NULL UNIQUE
);

INSERT INTO p3_16_schema_guard (token) VALUES (1);

WITH expected_exam_results (
    cid, name, type, not_null, default_value, primary_key
) AS (
    VALUES
        (0, 'id', 'INTEGER', 0, NULL, 1),
        (1, 'student_name', 'TEXT', 1, NULL, 0),
        (2, 'front_image', 'TEXT', 1, NULL, 0),
        (3, 'back_image', 'TEXT', 1, NULL, 0),
        (4, 'total_score', 'REAL', 1, NULL, 0),
        (5, 'student_score', 'REAL', 1, NULL, 0),
        (6, 'needs_human_review', 'INTEGER', 1, NULL, 0),
        (7, 'raw_json', 'TEXT', 1, NULL, 0),
        (8, 'created_at', 'TEXT', 1, 'datetime(''now'',''localtime'')', 0)
),
expected_grading_details (
    cid, name, type, not_null, default_value, primary_key
) AS (
    VALUES
        (0, 'id', 'INTEGER', 0, NULL, 1),
        (1, 'exam_result_id', 'INTEGER', 1, NULL, 0),
        (2, 'question_id', 'TEXT', 1, NULL, 0),
        (3, 'score_awarded', 'REAL', 1, NULL, 0),
        (4, 'deduction_reason', 'TEXT', 0, NULL, 0),
        (5, 'knowledge_id', 'TEXT', 1, NULL, 0),
        (6, 'knowledge_ids', 'TEXT', 0, NULL, 0),
        (7, 'error_category', 'TEXT', 0, NULL, 0),
        (8, 'error_summary', 'TEXT', 0, NULL, 0),
        (9, 'confidence_score', 'REAL', 0, NULL, 0)
)
INSERT INTO p3_16_schema_guard (token)
SELECT 1
WHERE
    (
        SELECT COUNT(*) FROM pragma_table_info('exam_results')
    ) <> (
        SELECT COUNT(*) FROM expected_exam_results
    )
    OR EXISTS (
        SELECT 1
        FROM expected_exam_results AS expected
        LEFT JOIN pragma_table_info('exam_results') AS actual
          ON actual.cid = expected.cid
        WHERE
            actual.name IS NOT expected.name
            OR actual.type IS NOT expected.type
            OR actual."notnull" IS NOT expected.not_null
            OR actual.dflt_value IS NOT expected.default_value
            OR actual.pk IS NOT expected.primary_key
    )
    OR (
        SELECT COUNT(*) FROM pragma_table_info('grading_details')
    ) <> (
        SELECT COUNT(*) FROM expected_grading_details
    )
    OR EXISTS (
        SELECT 1
        FROM expected_grading_details AS expected
        LEFT JOIN pragma_table_info('grading_details') AS actual
          ON actual.cid = expected.cid
        WHERE
            actual.name IS NOT expected.name
            OR actual.type IS NOT expected.type
            OR actual."notnull" IS NOT expected.not_null
            OR actual.dflt_value IS NOT expected.default_value
            OR actual.pk IS NOT expected.primary_key
    )
    OR (
        SELECT COUNT(*) FROM pragma_foreign_key_list('grading_details')
    ) <> 1
    OR NOT EXISTS (
        SELECT 1
        FROM pragma_foreign_key_list('grading_details')
        WHERE
            "table" = 'exam_results'
            AND "from" = 'exam_result_id'
            AND "to" = 'id'
            AND on_update = 'NO ACTION'
            AND on_delete = 'NO ACTION'
            AND match = 'NONE'
    )
LIMIT 1;

DROP TABLE grading_details;
DROP TABLE exam_results;
