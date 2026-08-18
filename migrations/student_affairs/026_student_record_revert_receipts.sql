-- 学生档案合并回执记录合并前快照与撤回时间，支持一键撤回。

ALTER TABLE handoff_adoption_receipts ADD COLUMN profile_snapshot_object_id TEXT;
ALTER TABLE handoff_adoption_receipts ADD COLUMN reverted_at TEXT;
