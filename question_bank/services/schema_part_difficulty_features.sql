-- 标准难度（八上重打标签方案第 4 节）：逐小问 SOLO 与难度特征的独立评估。
-- 与 migrations/question_bank/043_add_question_part_difficulty_features.sql 相同；
-- 保留给需要在隔离副本上补建该表的脚本使用。
-- 逐小问难度的唯一来源：掌握度、推荐与组卷的小问难度都读这里的公式分。
-- 存特征与公式版本号，题目内容指纹（source_content_hash）变化后由读取侧标记"需重评"。
CREATE TABLE IF NOT EXISTS question_part_difficulty_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    part_id TEXT NOT NULL,
    features_json TEXT NOT NULL CHECK (json_valid(features_json)),
    direct_difficulty INTEGER CHECK (direct_difficulty IS NULL OR direct_difficulty BETWEEN 1 AND 10),
    formula_difficulty REAL,
    formula_version TEXT NOT NULL,
    source_content_hash TEXT NOT NULL DEFAULT '',
    model_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    CHECK (TRIM(part_id) <> ''),
    CHECK (TRIM(formula_version) <> '')
);

-- 每题每小问至多一条有效记录；重打时旧记录先置 is_active=0。
CREATE UNIQUE INDEX IF NOT EXISTS idx_part_difficulty_features_active
ON question_part_difficulty_features(question_id, part_id)
WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS idx_part_difficulty_features_question
ON question_part_difficulty_features(question_id);
