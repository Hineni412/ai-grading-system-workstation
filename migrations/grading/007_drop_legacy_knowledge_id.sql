-- migration-policy: rebuild-tables session_details
-- P3-14: remove the retired generated knowledge_id compatibility column.

CREATE TEMP TABLE p3_14_schema_guard (
    token INTEGER NOT NULL UNIQUE
);

INSERT INTO p3_14_schema_guard (token) VALUES (1);

WITH expected_columns (
    cid, name, type, not_null, default_value, primary_key, hidden
) AS (
    VALUES
        (0, 'id', 'INTEGER', 0, NULL, 1, 0),
        (1, 'result_id', 'INTEGER', 1, NULL, 0, 0),
        (2, 'question_id', 'TEXT', 1, NULL, 0, 0),
        (3, 'score_awarded', 'REAL', 1, NULL, 0, 0),
        (4, 'deduction_reason', 'TEXT', 0, NULL, 0, 0),
        (5, 'knowledge_ids', 'TEXT', 1, NULL, 0, 0),
        (6, 'knowledge_id', 'TEXT', 1, NULL, 0, 3),
        (7, 'error_category', 'TEXT', 0, NULL, 0, 0),
        (8, 'error_summary', 'TEXT', 0, NULL, 0, 0),
        (9, 'confidence_score', 'REAL', 0, NULL, 0, 0),
        (10, 'secondary_errors_json', 'TEXT', 1, '''[]''', 0, 0)
)
INSERT INTO p3_14_schema_guard (token)
SELECT 1
WHERE
    (
        SELECT COUNT(*)
        FROM pragma_table_xinfo('session_details')
    ) <> (
        SELECT COUNT(*)
        FROM expected_columns
    )
    OR EXISTS (
        SELECT 1
        FROM expected_columns AS expected
        LEFT JOIN pragma_table_xinfo('session_details') AS actual
          ON actual.cid = expected.cid
        WHERE
            actual.name IS NOT expected.name
            OR actual.type IS NOT expected.type
            OR actual."notnull" IS NOT expected.not_null
            OR actual.dflt_value IS NOT expected.default_value
            OR actual.pk IS NOT expected.primary_key
            OR actual.hidden IS NOT expected.hidden
    )
    OR (
        SELECT COUNT(*)
        FROM pragma_foreign_key_list('session_details')
    ) <> 1
    OR NOT EXISTS (
        SELECT 1
        FROM pragma_foreign_key_list('session_details')
        WHERE
            "table" = 'session_results'
            AND "from" = 'result_id'
            AND "to" = 'id'
            AND on_update = 'NO ACTION'
            AND on_delete = 'NO ACTION'
            AND match = 'NONE'
    )
    OR (
        SELECT COUNT(*)
        FROM pragma_index_list('session_details')
    ) <> 2
    OR NOT EXISTS (
        SELECT 1
        FROM pragma_index_list('session_details')
        WHERE
            name = 'idx_session_details_question'
            AND "unique" = 0
            AND origin = 'c'
            AND partial = 0
    )
    OR NOT EXISTS (
        SELECT 1
        FROM pragma_index_list('session_details')
        WHERE
            name = 'idx_session_details_result'
            AND "unique" = 0
            AND origin = 'c'
            AND partial = 0
    )
    OR (
        SELECT COUNT(*)
        FROM sqlite_schema
        WHERE type = 'trigger' AND tbl_name = 'session_details'
    ) <> 2
    OR NOT EXISTS (
        SELECT 1
        FROM sqlite_schema
        WHERE
            type = 'trigger'
            AND tbl_name = 'session_details'
            AND name = 'session_details_knowledge_ids_valid_insert'
    )
    OR NOT EXISTS (
        SELECT 1
        FROM sqlite_schema
        WHERE
            type = 'trigger'
            AND tbl_name = 'session_details'
            AND name = 'session_details_knowledge_ids_valid_update'
    )
LIMIT 1;

CREATE TEMP TABLE p3_14_validation_guard (
    token INTEGER NOT NULL UNIQUE
);

INSERT INTO p3_14_validation_guard (token) VALUES (1);

INSERT INTO p3_14_validation_guard (token)
SELECT 1
FROM session_details
WHERE
    knowledge_ids IS NULL
    OR TRIM(knowledge_ids) = ''
    OR NOT json_valid(knowledge_ids)
    OR json_type(knowledge_ids) <> 'array'
    OR json_array_length(knowledge_ids) = 0
    OR json_type(knowledge_ids, '$[0]') <> 'text'
    OR TRIM(json_extract(knowledge_ids, '$[0]')) = ''
    OR EXISTS (
        SELECT 1
        FROM json_each(knowledge_ids)
        WHERE type <> 'text' OR TRIM(CAST(value AS TEXT)) = ''
    )
LIMIT 1;

CREATE TEMP TABLE p3_14_sequence_guard (
    seq INTEGER NOT NULL
);

INSERT INTO p3_14_sequence_guard (seq)
SELECT seq
FROM sqlite_sequence
WHERE name = 'session_details';

CREATE TABLE session_details_p3_14_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    score_awarded REAL NOT NULL,
    deduction_reason TEXT,
    knowledge_ids TEXT NOT NULL
        CHECK (
            json_valid(knowledge_ids)
            AND json_type(knowledge_ids) = 'array'
            AND json_array_length(knowledge_ids) > 0
            AND json_type(knowledge_ids, '$[0]') = 'text'
            AND TRIM(json_extract(knowledge_ids, '$[0]')) <> ''
        ),
    error_category TEXT,
    error_summary TEXT,
    confidence_score REAL,
    secondary_errors_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY(result_id) REFERENCES session_results(id)
);

INSERT INTO session_details_p3_14_new (
    id, result_id, question_id, score_awarded, deduction_reason,
    knowledge_ids, error_category, error_summary, confidence_score,
    secondary_errors_json
)
SELECT
    id, result_id, question_id, score_awarded, deduction_reason,
    knowledge_ids, error_category, error_summary, confidence_score,
    secondary_errors_json
FROM session_details;

DROP TABLE session_details;

ALTER TABLE session_details_p3_14_new RENAME TO session_details;

UPDATE sqlite_sequence
SET seq = MAX(
    seq,
    COALESCE((SELECT seq FROM p3_14_sequence_guard), seq)
)
WHERE name = 'session_details';

INSERT INTO sqlite_sequence (name, seq)
SELECT 'session_details', preserved.seq
FROM p3_14_sequence_guard AS preserved
WHERE NOT EXISTS (
    SELECT 1
    FROM sqlite_sequence
    WHERE name = 'session_details'
);

CREATE INDEX idx_session_details_question
ON session_details(question_id);

CREATE INDEX idx_session_details_result
ON session_details(result_id);

CREATE TRIGGER session_details_knowledge_ids_valid_insert
BEFORE INSERT ON session_details
WHEN EXISTS (
    SELECT 1
    FROM json_each(NEW.knowledge_ids)
    WHERE type <> 'text' OR TRIM(CAST(value AS TEXT)) = ''
)
BEGIN
    SELECT RAISE(ABORT, 'session_details.knowledge_ids items must be nonblank text');
END;

CREATE TRIGGER session_details_knowledge_ids_valid_update
BEFORE UPDATE OF knowledge_ids ON session_details
WHEN EXISTS (
    SELECT 1
    FROM json_each(NEW.knowledge_ids)
    WHERE type <> 'text' OR TRIM(CAST(value AS TEXT)) = ''
)
BEGIN
    SELECT RAISE(ABORT, 'session_details.knowledge_ids items must be nonblank text');
END;
