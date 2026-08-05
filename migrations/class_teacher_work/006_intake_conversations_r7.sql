CREATE TABLE IF NOT EXISTS class_teacher_preferences (
    preference_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_conversations (
    conversation_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (state IN (
        'collecting', 'ai_running', 'needs_input', 'handoff_ready',
        'draft_opened', 'teacher_confirmed', 'failed',
        'manual_routing', 'abandoned'
    )),
    homeroom_class TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intake_conversations_updated
    ON intake_conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS intake_turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    operation_id TEXT NOT NULL UNIQUE,
    source_revision INTEGER NOT NULL CHECK (source_revision >= 1),
    teacher_message TEXT NOT NULL,
    assistant_message TEXT,
    clarification_questions_json TEXT NOT NULL DEFAULT '[]',
    task_id TEXT,
    task_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(conversation_id, sequence),
    FOREIGN KEY(conversation_id) REFERENCES intake_conversations(conversation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_intake_turns_conversation
    ON intake_turns(conversation_id, sequence);

CREATE TABLE IF NOT EXISTS intake_drafts (
    draft_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    handling_mode TEXT NOT NULL CHECK (handling_mode IN ('record', 'plan_calendar', 'sop')),
    intent TEXT NOT NULL,
    destination_key TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    content_json TEXT NOT NULL,
    subject_refs_json TEXT NOT NULL DEFAULT '[]',
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL CHECK (state IN ('open', 'adopted', 'discarded', 'stale')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES intake_conversations(conversation_id)
        ON DELETE CASCADE,
    FOREIGN KEY(turn_id) REFERENCES intake_turns(turn_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS intake_handoffs (
    handoff_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE,
    adoption_id TEXT NOT NULL UNIQUE,
    adoption_state TEXT NOT NULL CHECK (adoption_state IN (
        'pending', 'opened', 'adoption_started', 'adopted', 'discarded', 'stale'
    )),
    target_revision TEXT,
    formal_object_type TEXT,
    formal_object_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(draft_id) REFERENCES intake_drafts(draft_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_intake_handoffs_state
    ON intake_handoffs(adoption_state, updated_at DESC);

CREATE TABLE IF NOT EXISTS intake_draft_revision_requests (
    request_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    handoff_id TEXT NOT NULL,
    source_draft_revision INTEGER NOT NULL CHECK (source_draft_revision >= 1),
    instruction TEXT NOT NULL,
    task_id TEXT,
    task_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(handoff_id) REFERENCES intake_handoffs(handoff_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_intake_draft_revision_requests_handoff
    ON intake_draft_revision_requests(handoff_id, created_at DESC);
