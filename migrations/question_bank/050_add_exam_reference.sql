ALTER TABLE papers ADD COLUMN is_exam_reference INTEGER NOT NULL DEFAULT 0 CHECK (is_exam_reference IN (0, 1));
ALTER TABLE papers ADD COLUMN year_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (year_confirmed IN (0, 1));
