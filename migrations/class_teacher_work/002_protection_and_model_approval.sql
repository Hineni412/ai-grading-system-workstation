CREATE TABLE IF NOT EXISTS sensitive_protection (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    mode TEXT NOT NULL CHECK (mode = 'pin_dpapi_current_user_v2'),
    state TEXT NOT NULL CHECK (state = 'active'),
    pin_salt BLOB NOT NULL,
    protected_secret BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sensitive_protection_pending (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    mode TEXT NOT NULL CHECK (mode = 'pin_dpapi_current_user_v2'),
    pin_salt BLOB NOT NULL,
    protected_secret BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_approval_operations (
    preview_id TEXT PRIMARY KEY,
    operation_id TEXT UNIQUE,
    purpose TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (
        classification IN ('ordinary', 'restricted')
    ),
    payload_object_id TEXT NOT NULL,
    result_object_id TEXT,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'previewed', 'confirmed', 'claimed', 'succeeded',
            'failed_before_send', 'result_unknown',
            'cancelled_before_send'
        )
    ),
    removed_categories_json TEXT NOT NULL,
    physical_request_count INTEGER NOT NULL DEFAULT 0
        CHECK (physical_request_count IN (0, 1)),
    error_category TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_approval_state
    ON model_approval_operations(state, expires_at);
