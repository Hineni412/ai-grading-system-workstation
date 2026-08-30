-- Link an imported question to the existing bank question it duplicates exactly.
-- Exact duplicates stay imported as their own rows; this table records the
-- relationship so analysis products (tags, judgment points, evidence) can be
-- reused and the relationship can be surfaced later.
CREATE TABLE question_duplicate_links (
    question_id INTEGER PRIMARY KEY,
    duplicate_of_question_id INTEGER NOT NULL,
    match_kind TEXT NOT NULL DEFAULT 'exact'
        CHECK (match_kind IN ('exact')),
    signature TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(question_id) REFERENCES questions(id),
    FOREIGN KEY(duplicate_of_question_id) REFERENCES questions(id)
);

CREATE INDEX idx_question_duplicate_links_source
    ON question_duplicate_links(duplicate_of_question_id);
