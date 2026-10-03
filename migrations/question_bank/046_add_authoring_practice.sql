-- 命题练习（authoring practice）：教师的拆解/改编命题作品与版本，
-- 独立于题库正文。作品只冻结母题快照，不回写 questions/question_tags 等
-- 题库表，也不进入题库列表、组卷候选、训练推荐或掌握度。
-- authoring_reviews / authoring_assets 供后续 AI 评审阶段使用，本阶段仅建表。
CREATE TABLE IF NOT EXISTS authoring_works (
    work_id              TEXT PRIMARY KEY,
    kind                 TEXT NOT NULL
        CHECK (kind IN ('decompose', 'adapt')),
    source_question_id   INTEGER REFERENCES questions(id),
    source_snapshot_json TEXT NOT NULL CHECK (json_valid(source_snapshot_json)),
    task_card_json       TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(task_card_json)),
    title                TEXT NOT NULL DEFAULT '',
    current_version      INTEGER NOT NULL DEFAULT 0
        CHECK (current_version >= 0),
    is_deleted           INTEGER NOT NULL DEFAULT 0
        CHECK (is_deleted IN (0, 1)),
    create_token         TEXT UNIQUE,
    created_at           TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_authoring_works_kind_updated
    ON authoring_works(kind, is_deleted, updated_at DESC);

CREATE TABLE IF NOT EXISTS authoring_work_versions (
    work_id         TEXT NOT NULL REFERENCES authoring_works(work_id),
    version_no      INTEGER NOT NULL CHECK (version_no > 0),
    content_json    TEXT NOT NULL CHECK (json_valid(content_json)),
    operation_token TEXT UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (work_id, version_no)
);

CREATE TABLE IF NOT EXISTS authoring_reviews (
    review_id          TEXT PRIMARY KEY,
    work_id            TEXT NOT NULL REFERENCES authoring_works(work_id),
    version_no         INTEGER NOT NULL,
    status             TEXT NOT NULL
        CHECK (status IN ('running', 'succeeded', 'failed', 'unknown')),
    operation_token    TEXT UNIQUE,
    result_json        TEXT
        CHECK (result_json IS NULL OR json_valid(result_json)),
    error_category     TEXT,
    model_summary_json TEXT
        CHECK (model_summary_json IS NULL OR json_valid(model_summary_json)),
    created_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_authoring_reviews_work
    ON authoring_reviews(work_id, version_no);

CREATE TABLE IF NOT EXISTS authoring_assets (
    asset_id      TEXT PRIMARY KEY,
    work_id       TEXT NOT NULL REFERENCES authoring_works(work_id),
    role          TEXT NOT NULL CHECK (role IN ('photo', 'figure')),
    relative_path TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_authoring_assets_work
    ON authoring_assets(work_id);
