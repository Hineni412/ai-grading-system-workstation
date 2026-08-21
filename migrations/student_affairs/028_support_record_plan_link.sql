-- 支持记录可归属到支持方案：教师在方案下记的行动日志通过可空 plan_id
-- 关联到同一名学生的方案；普通观察记录 plan_id 为空，语义不变。
-- 归属校验（方案存在且属于同一学生）在写入服务端完成，旧行全部为空。

ALTER TABLE support_records
ADD COLUMN plan_id TEXT REFERENCES support_plans(support_plan_id);

CREATE INDEX IF NOT EXISTS idx_support_records_plan
    ON support_records(plan_id);
