CREATE TABLE teaching_prep_ai_task_results (
    task_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    task_kind TEXT NOT NULL,
    source_ref_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    context_refs_json TEXT NOT NULL,
    proposal_ref_id TEXT NOT NULL,
    proposal_revision TEXT NOT NULL,
    handoffs_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE teaching_prep_ai_adopted_items (
    id TEXT PRIMARY KEY,
    adoption_id TEXT NOT NULL UNIQUE,
    task_kind TEXT NOT NULL,
    proposal_ref_id TEXT NOT NULL,
    proposal_revision TEXT NOT NULL,
    target_ref_id TEXT NOT NULL,
    target_revision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready_for_teacher_review'
        CHECK(status = 'ready_for_teacher_review'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE teaching_prep_ai_adoption_receipts (
    adoption_id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL,
    draft_revision TEXT NOT NULL,
    target_revision TEXT NOT NULL,
    object_ref TEXT NOT NULL,
    receipt_revision TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
