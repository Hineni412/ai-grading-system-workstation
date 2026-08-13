-- 初始化迁移：建立 schema_migrations 跟踪表。
-- 这是"零号迁移"，标记题库数据库已纳入迁移体系。
-- 不修改已有业务表。

CREATE TABLE IF NOT EXISTS schema_migrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_name TEXT NOT NULL UNIQUE,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    checksum    TEXT,
    success     INTEGER NOT NULL DEFAULT 1
);
