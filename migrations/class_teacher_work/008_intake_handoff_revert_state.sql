-- migration-policy: rebuild-tables intake_handoffs
-- 学生档案更新支持一键撤回：adoption_state 增加 'reverted'。

CREATE TABLE intake_handoffs_rebuilt (
    handoff_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE,
    adoption_id TEXT NOT NULL UNIQUE,
    common_handoff_id TEXT UNIQUE,
    adoption_state TEXT NOT NULL CHECK (adoption_state IN (
        'pending', 'opened', 'adoption_started', 'adopted', 'reverted', 'discarded', 'stale'
    )),
    target_revision TEXT,
    formal_object_type TEXT,
    formal_object_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(draft_id) REFERENCES intake_drafts(draft_id)
        ON DELETE CASCADE
);

INSERT INTO intake_handoffs_rebuilt (
    handoff_id, draft_id, adoption_id, common_handoff_id, adoption_state,
    target_revision, formal_object_type, formal_object_id, created_at, updated_at
)
SELECT handoff_id, draft_id, adoption_id, common_handoff_id, adoption_state,
       target_revision, formal_object_type, formal_object_id, created_at, updated_at
FROM intake_handoffs;

DROP TABLE intake_handoffs;

ALTER TABLE intake_handoffs_rebuilt RENAME TO intake_handoffs;

CREATE INDEX idx_intake_handoffs_state
    ON intake_handoffs(adoption_state, updated_at DESC);
