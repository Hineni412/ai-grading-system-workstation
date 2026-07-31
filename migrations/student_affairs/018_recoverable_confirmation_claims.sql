ALTER TABLE confirmation_claims
    ADD COLUMN state TEXT NOT NULL DEFAULT 'claimed'
    CHECK (state IN ('claimed', 'target_created', 'completed'));

ALTER TABLE confirmation_claims
    ADD COLUMN target_kind TEXT;

ALTER TABLE confirmation_claims
    ADD COLUMN target_id TEXT;

ALTER TABLE confirmation_claims
    ADD COLUMN updated_at TEXT;
