---
status: accepted
---

# Keep question-bank sync state on the grading session

Keep `question_bank_sync_state`, `question_bank_sync_details_json`, `question_bank_sync_error`, and `question_bank_sync_updated_at` on `grading_sessions` rather than moving them into a separate status table. The state is one-to-one with the session and is atomically reset when its source paper changes; measurements at 2, 19, and 1000 rows found that splitting added storage and changed a single primary-key lookup into two lookups plus a join, without improving the session-summary query, so the extra seam would reduce locality without measured benefit.
