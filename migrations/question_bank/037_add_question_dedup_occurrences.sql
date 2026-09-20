-- Persistent question content index and per-paper occurrence records.
--
-- ``question_content_index`` stores each canonical question's exact-duplicate
-- identity key once, so import-time dedup looks up a precomputed index instead
-- of decoding every bank image on each batch. Rows are written when a question
-- is created and refreshed when its content is revised.
--
-- ``paper_question_occurrences`` records that an imported paper contains a
-- question whose canonical bank row already exists. Reused questions keep the
-- importing paper's own question_number here instead of inserting a second
-- ``questions`` row, so their tags, answers and analysis products are shared
-- with the canonical question rather than re-created.
CREATE TABLE question_content_index (
    question_id INTEGER PRIMARY KEY,
    content_key TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(question_id) REFERENCES questions(id)
);

CREATE INDEX idx_question_content_index_key
    ON question_content_index(content_key);

CREATE TABLE paper_question_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    question_number TEXT NOT NULL,
    match_kind TEXT NOT NULL DEFAULT 'exact'
        CHECK (match_kind IN ('exact')),
    signature TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(paper_id) REFERENCES papers(id),
    FOREIGN KEY(question_id) REFERENCES questions(id)
);

CREATE UNIQUE INDEX uq_paper_question_occurrences
    ON paper_question_occurrences(paper_id, question_number);

CREATE INDEX idx_paper_question_occurrences_question
    ON paper_question_occurrences(question_id);
