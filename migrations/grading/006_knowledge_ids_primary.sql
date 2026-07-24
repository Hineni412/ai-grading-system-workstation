-- migration-policy: rebuild-tables session_details
-- P3-13: make knowledge_ids the stored truth and knowledge_id read-only.

ALTER TABLE session_details
ADD COLUMN secondary_errors_json TEXT NOT NULL DEFAULT '[]';

CREATE TEMP TABLE p3_13_validation_guard (
    token INTEGER NOT NULL UNIQUE
);

INSERT INTO p3_13_validation_guard (token) VALUES (1);

INSERT INTO p3_13_validation_guard (token)
SELECT 1
FROM session_details
WHERE
    CASE
        WHEN knowledge_id IS NULL OR TRIM(knowledge_id) = '' THEN 1
        WHEN knowledge_ids IS NULL OR TRIM(knowledge_ids) = '' THEN 0
        WHEN NOT json_valid(knowledge_ids) THEN 1
        WHEN json_type(knowledge_ids) <> 'array' THEN 1
        WHEN json_array_length(knowledge_ids) = 0 THEN 1
        WHEN EXISTS (
            SELECT 1
            FROM json_each(knowledge_ids)
            WHERE type <> 'text' OR TRIM(CAST(value AS TEXT)) = ''
        ) THEN 1
        WHEN NOT EXISTS (
            SELECT 1
            FROM json_each(knowledge_ids)
            WHERE TRIM(CAST(value AS TEXT)) = TRIM(session_details.knowledge_id)
        ) THEN 1
        ELSE 0
    END = 1
LIMIT 1;

CREATE TEMP TABLE p3_13_sequence_guard (
    seq INTEGER NOT NULL
);

INSERT INTO p3_13_sequence_guard (seq)
SELECT seq
FROM sqlite_sequence
WHERE name = 'session_details';

CREATE TABLE session_details_p3_13_new (
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
    knowledge_id TEXT GENERATED ALWAYS AS (
        json_extract(knowledge_ids, '$[0]')
    ) STORED NOT NULL,
    error_category TEXT,
    error_summary TEXT,
    confidence_score REAL,
    secondary_errors_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY(result_id) REFERENCES session_results(id)
);

INSERT INTO session_details_p3_13_new (
    id, result_id, question_id, score_awarded, deduction_reason,
    knowledge_ids, error_category, error_summary, confidence_score,
    secondary_errors_json
)
SELECT
    id, result_id, question_id, score_awarded, deduction_reason,
    CASE
        WHEN knowledge_ids IS NULL OR TRIM(knowledge_ids) = ''
            THEN json_array(TRIM(knowledge_id))
        ELSE knowledge_ids
    END,
    error_category, error_summary, confidence_score, secondary_errors_json
FROM session_details;

DROP TABLE session_details;

ALTER TABLE session_details_p3_13_new RENAME TO session_details;

UPDATE sqlite_sequence
SET seq = MAX(
    seq,
    COALESCE((SELECT seq FROM p3_13_sequence_guard), seq)
)
WHERE name = 'session_details';

INSERT INTO sqlite_sequence (name, seq)
SELECT 'session_details', preserved.seq
FROM p3_13_sequence_guard AS preserved
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
