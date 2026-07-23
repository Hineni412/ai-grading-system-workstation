-- 旧 runtime JobStore 曾为已有 jobs 表补此列；迁移权威化后改由 forward migration 承接。
ALTER TABLE jobs ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}';
