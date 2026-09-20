-- Invalidate only content identities whose question or source assets changed.
ALTER TABLE question_content_index ADD COLUMN source_revision TEXT NOT NULL DEFAULT '';
