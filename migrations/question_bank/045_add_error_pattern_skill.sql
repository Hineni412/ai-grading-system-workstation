-- 典型错法可关联技能：skill_key 存当前知识标准中技能术语的规范 id（sk_*）。
-- 空值表示教师未显式关联；判定点（step）触发位在读取侧仍可由该判定点
-- 唯一直达的技能链接推导，推导结果不写回本列。
ALTER TABLE question_error_patterns ADD COLUMN skill_key TEXT;
