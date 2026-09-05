-- 题型归一：解答题的"画图/计算/证明"子类从 question_type 枚举迁移为 special_type 标签。
-- 标签 confidence=0.7 表示迁移来源、低于人工确认；NOT EXISTS 保证重复执行幂等。
-- 裸"解答题"不补猜子类，保持未标注。
INSERT INTO question_tags (question_id, tag_type, tag_value, confidence, source, model_name)
SELECT id, 'special_type', '画图', 0.7, 'question_type_migration', NULL
FROM questions
WHERE question_type = '解答题（画图）'
  AND NOT EXISTS (
      SELECT 1 FROM question_tags t
      WHERE t.question_id = questions.id
        AND t.tag_type = 'special_type'
        AND t.tag_value = '画图'
  );

INSERT INTO question_tags (question_id, tag_type, tag_value, confidence, source, model_name)
SELECT id, 'special_type', '计算', 0.7, 'question_type_migration', NULL
FROM questions
WHERE question_type = '解答题（计算）'
  AND NOT EXISTS (
      SELECT 1 FROM question_tags t
      WHERE t.question_id = questions.id
        AND t.tag_type = 'special_type'
        AND t.tag_value = '计算'
  );

INSERT INTO question_tags (question_id, tag_type, tag_value, confidence, source, model_name)
SELECT id, 'special_type', '证明', 0.7, 'question_type_migration', NULL
FROM questions
WHERE question_type = '解答题（证明）'
  AND NOT EXISTS (
      SELECT 1 FROM question_tags t
      WHERE t.question_id = questions.id
        AND t.tag_type = 'special_type'
        AND t.tag_value = '证明'
  );

UPDATE questions
SET question_type = '解答题'
WHERE question_type IN ('解答题（画图）', '解答题（计算）', '解答题（证明）');
