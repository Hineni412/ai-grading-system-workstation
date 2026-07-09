-- 历史占位迁移（保留编号，当前为 no-op）。
--
-- 这个文件最初是用于 questions.teacher_review_status 的示例迁移，但当前运行时
-- schema、真实 question_bank.db 与第一方代码均未使用该列。WP0.4 的目标是让
-- "空库 + 全部迁移" 与 "空库 + 运行时初始化" 等价，因此不能再由旧占位迁移
-- 向新库追加未使用字段。
--
-- 文件保留用于维持迁移编号连续性；真实库落地时会通过 schema_migrations 打标。
SELECT 1;
