-- SOP 事务的学生档案更新只以待确认草稿形式存在，教师逐人确认后才写入档案。
-- 表结构仿 sop_step_drafts（020 迁移）：元数据行 + encrypted_objects payload + revision。

CREATE TABLE IF NOT EXISTS affair_profile_update_drafts (
    draft_id TEXT PRIMARY KEY,
    affair_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'confirmed', 'discarded')
    ),
    payload_object_id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    UNIQUE(affair_id, subject_id),
    FOREIGN KEY(affair_id) REFERENCES affairs(affair_id) ON DELETE CASCADE,
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_affair_profile_update_drafts_state
    ON affair_profile_update_drafts(affair_id, state);
