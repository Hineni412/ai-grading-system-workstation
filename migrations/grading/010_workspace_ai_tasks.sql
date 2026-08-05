-- TW-F1: metadata-only teacher-workspace AI task coordination.
CREATE TABLE workspace_ai_tasks (
    task_id TEXT PRIMARY KEY CHECK (TRIM(task_id) <> ''),
    operation_id TEXT NOT NULL UNIQUE CHECK (TRIM(operation_id) <> ''),
    module TEXT NOT NULL CHECK (module IN ('teaching_prep', 'class_teacher')),
    task_kind TEXT NOT NULL CHECK (TRIM(task_kind) <> ''),
    source_kind TEXT NOT NULL CHECK (TRIM(source_kind) <> ''),
    source_ref_id TEXT NOT NULL CHECK (TRIM(source_ref_id) <> ''),
    source_revision TEXT NOT NULL CHECK (TRIM(source_revision) <> ''),
    context_refs_json TEXT NOT NULL DEFAULT '[]',
    prompt_contract_version TEXT NOT NULL CHECK (TRIM(prompt_contract_version) <> ''),
    request_fingerprint TEXT NOT NULL CHECK (LENGTH(request_fingerprint) = 64),
    model_destination_fingerprint TEXT NOT NULL
        CHECK (LENGTH(model_destination_fingerprint) = 64),
    return_target TEXT NOT NULL CHECK (TRIM(return_target) <> ''),
    status TEXT NOT NULL DEFAULT 'prepared'
        CHECK (status IN (
            'prepared', 'queued', 'running', 'needs_input', 'proposal_ready',
            'failed_before_dispatch', 'failed', 'result_unknown',
            'invalid_result', 'cancelled_before_dispatch', 'discarded'
        )),
    phase TEXT NOT NULL DEFAULT 'prepared'
        CHECK (phase IN (
            'prepared', 'queued', 'claimed', 'send_attempt_reserved',
            'validating', 'handoff_ready', 'finished'
        )),
    progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    send_attempt_count INTEGER NOT NULL DEFAULT 0
        CHECK (send_attempt_count IN (0, 1)),
    dispatch_evidence TEXT NOT NULL DEFAULT 'not_started'
        CHECK (dispatch_evidence IN (
            'not_started', 'may_have_started', 'response_persisted'
        )),
    cancel_requested INTEGER NOT NULL DEFAULT 0
        CHECK (cancel_requested IN (0, 1)),
    proposal_ref_id TEXT,
    proposal_revision TEXT,
    job_id INTEGER UNIQUE REFERENCES jobs(id),
    error_code TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT
);

CREATE INDEX idx_workspace_ai_tasks_status
ON workspace_ai_tasks(status, updated_at DESC);

CREATE INDEX idx_workspace_ai_tasks_source
ON workspace_ai_tasks(module, source_kind, source_ref_id, updated_at DESC);

CREATE TABLE workspace_ai_handoffs (
    handoff_id TEXT PRIMARY KEY CHECK (TRIM(handoff_id) <> ''),
    task_id TEXT NOT NULL REFERENCES workspace_ai_tasks(task_id) ON DELETE CASCADE,
    work_item_id TEXT NOT NULL CHECK (TRIM(work_item_id) <> ''),
    module TEXT NOT NULL CHECK (module IN ('teaching_prep', 'class_teacher')),
    intent TEXT NOT NULL CHECK (intent IN ('create', 'append', 'follow_up', 'plan', 'review')),
    handling_mode TEXT NOT NULL CHECK (handling_mode IN ('record', 'plan_calendar', 'sop')),
    destination_key TEXT NOT NULL CHECK (TRIM(destination_key) <> ''),
    subject_refs_json TEXT NOT NULL DEFAULT '[]',
    draft_ref_id TEXT NOT NULL CHECK (TRIM(draft_ref_id) <> ''),
    draft_revision TEXT NOT NULL CHECK (TRIM(draft_revision) <> ''),
    adoption_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (adoption_state IN (
            'pending', 'opened', 'adoption_started', 'adopted',
            'discarded', 'stale'
        )),
    prefill_keys_json TEXT NOT NULL DEFAULT '[]',
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    source_turn_id TEXT,
    return_destination_key TEXT NOT NULL,
    return_focus_ref TEXT,
    expires_on_source_change INTEGER NOT NULL DEFAULT 1
        CHECK (expires_on_source_change IN (0, 1)),
    adoption_id TEXT UNIQUE,
    target_revision TEXT,
    adopted_object_ref TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(task_id, work_item_id)
);

CREATE INDEX idx_workspace_ai_handoffs_task
ON workspace_ai_handoffs(task_id, created_at);
