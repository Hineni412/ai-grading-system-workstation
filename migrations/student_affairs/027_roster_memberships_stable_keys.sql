-- migration-policy: rebuild-tables class_roster_memberships
-- 花名册成员表退化为纯花名册状态表：键从成绩库内部编号（会失效的 volatile id）
-- 改为稳定学籍标识（班级|学号，学号空时 班级|姓名），不再承担身份映射，
-- subject_id 列废除；「学生 ↔ 档案」映射唯一走 student_subject_links。
-- 旧行换算：经 subject_id 找到身份链接，取其稳定标识明文指纹作为新键；
-- 链接缺失或指纹仍是旧二进制格式（需先运行身份修复工具）的行直接置
-- historical 并保留原键，不强行删除。

CREATE TABLE class_roster_memberships_rebuilt (
    source_student_key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('active', 'historical')),
    activated_at TEXT NOT NULL,
    historical_at TEXT,
    updated_at TEXT NOT NULL
);

INSERT INTO class_roster_memberships_rebuilt (
    source_student_key, state, activated_at, historical_at, updated_at
)
SELECT l.source_fingerprint, m.state, m.activated_at, m.historical_at, m.updated_at
FROM class_roster_memberships m
JOIN student_subject_links l ON l.subject_id = m.subject_id
WHERE typeof(l.source_fingerprint) = 'text';

INSERT INTO class_roster_memberships_rebuilt (
    source_student_key, state, activated_at, historical_at, updated_at
)
SELECT m.source_student_key, 'historical', m.activated_at,
       COALESCE(m.historical_at, m.updated_at), m.updated_at
FROM class_roster_memberships m
WHERE NOT EXISTS (
    SELECT 1 FROM student_subject_links l
    WHERE l.subject_id = m.subject_id AND typeof(l.source_fingerprint) = 'text'
);

DROP TABLE class_roster_memberships;

ALTER TABLE class_roster_memberships_rebuilt RENAME TO class_roster_memberships;

CREATE INDEX idx_class_roster_memberships_state
    ON class_roster_memberships(state, updated_at);
