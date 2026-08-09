-- Existing duplicate exams remain readable, but no new create or rename may
-- introduce another normalized duplicate.  Triggers are used instead of a
-- unique index so this migration is safe for installations that already have
-- historical duplicates requiring teacher cleanup.
CREATE TRIGGER IF NOT EXISTS trg_grading_sessions_unique_name_insert
BEFORE INSERT ON grading_sessions
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM grading_sessions
    WHERE lower(trim(session_name)) = lower(trim(NEW.session_name))
)
BEGIN
    SELECT RAISE(ABORT, 'session_name_conflict');
END;

CREATE TRIGGER IF NOT EXISTS trg_grading_sessions_unique_name_update
BEFORE UPDATE OF session_name ON grading_sessions
FOR EACH ROW
WHEN EXISTS (
    SELECT 1 FROM grading_sessions
    WHERE id <> OLD.id
      AND lower(trim(session_name)) = lower(trim(NEW.session_name))
)
BEGIN
    SELECT RAISE(ABORT, 'session_name_conflict');
END;
