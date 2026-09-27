-- migration-policy: drop-tables question_part_assessment_profiles
-- 小问难度档案退役：逐小问难度的唯一来源是 question_part_difficulty_features
-- 的公式分；模型小问估计档案不再使用。
-- 索引 idx_part_assessment_active 随表一并删除（迁移检查不允许单独 DROP INDEX）。
DROP TABLE IF EXISTS question_part_assessment_profiles;
