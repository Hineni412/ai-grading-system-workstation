-- Dual-track scores: keep each AI run's original scores beside teacher finals.
-- ai_score_awarded / ai_student_score stay NULL when no AI score exists.
ALTER TABLE session_details
ADD COLUMN ai_score_awarded REAL
CHECK (
    ai_score_awarded IS NULL
    OR (
        typeof(ai_score_awarded) IN ('integer', 'real')
        AND ai_score_awarded >= 0
    )
);

ALTER TABLE session_results
ADD COLUMN ai_student_score REAL
CHECK (
    ai_student_score IS NULL
    OR (
        typeof(ai_student_score) IN ('integer', 'real')
        AND ai_student_score >= 0
    )
);
