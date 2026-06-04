-- 示例迁移：给 questions 表添加 teacher_review_status 字段。
-- 用于标记教师是否已审核该题目的 AI 标签。
-- 非破坏性操作：只添加列，不删除或修改已有数据。
--
-- 可能的值: NULL(未审核), 'approved'(已通过), 'rejected'(已驳回), 'pending'(待审核)

-- safe: ADD COLUMN 如果列已存在 SQLite 会报错，迁移引擎会自动处理
ALTER TABLE questions ADD COLUMN teacher_review_status TEXT DEFAULT NULL;
