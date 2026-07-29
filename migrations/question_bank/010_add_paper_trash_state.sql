ALTER TABLE papers ADD COLUMN deleted_at TEXT;
ALTER TABLE papers ADD COLUMN deleted_from_status TEXT;
ALTER TABLE papers ADD COLUMN delete_operation_id TEXT;

ALTER TABLE questions ADD COLUMN paper_delete_operation_id TEXT;

CREATE INDEX IF NOT EXISTS idx_papers_import_status_updated
ON papers(import_status, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_questions_paper_delete_operation
ON questions(paper_id, paper_delete_operation_id);
