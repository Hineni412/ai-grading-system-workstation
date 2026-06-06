ALTER TABLE papers ADD COLUMN province TEXT DEFAULT NULL;
ALTER TABLE papers ADD COLUMN city TEXT DEFAULT NULL;

CREATE TABLE IF NOT EXISTS question_fingerprints (
    question_id INTEGER PRIMARY KEY,
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    base_fingerprint TEXT NOT NULL,
    style_features_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(question_id) REFERENCES questions(id)
);

CREATE INDEX IF NOT EXISTS idx_papers_exam_scope ON papers(exam_type, grade, semester, city);
CREATE INDEX IF NOT EXISTS idx_question_fingerprints_base ON question_fingerprints(base_fingerprint);

UPDATE papers
SET province = COALESCE(NULLIF(province, ''), '广东省'),
    city = COALESCE(NULLIF(city, ''), '深圳市')
WHERE title LIKE '%深圳%'
   OR source_file LIKE '%深圳%'
   OR district IN ('深圳市', '福田区', '罗湖区', '南山区', '宝安区', '龙岗区', '龙华区', '盐田区', '坪山区', '光明区', '大鹏新区');

UPDATE papers
SET semester = CASE
    WHEN title LIKE '%（上）%' OR title LIKE '%(上)%'
      OR title LIKE '%上学期%' OR title LIKE '%上册%' THEN '上学期'
    WHEN title LIKE '%（下）%' OR title LIKE '%(下)%'
      OR title LIKE '%下学期%' OR title LIKE '%下册%' THEN '下学期'
    ELSE semester
END
WHERE COALESCE(semester, '') = '';
