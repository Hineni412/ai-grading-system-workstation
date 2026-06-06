from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                source_file TEXT,
                year TEXT,
                province TEXT,
                city TEXT,
                district TEXT,
                exam_type TEXT,
                grade TEXT,
                semester TEXT,
                textbook_version TEXT,
                import_status TEXT,
                content_fingerprint TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER,
                question_number TEXT NOT NULL,
                question_type TEXT,
                question_text TEXT NOT NULL,
                answer_text TEXT,
                source_file TEXT,
                page_range TEXT,
                image_paths TEXT NOT NULL DEFAULT '[]',
                difficulty TEXT,
                typicality TEXT,
                reason TEXT,
                needs_review INTEGER NOT NULL DEFAULT 0,
                has_images INTEGER NOT NULL DEFAULT 0,
                needs_image_review INTEGER NOT NULL DEFAULT 0,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY(paper_id) REFERENCES papers(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS question_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                tag_type TEXT NOT NULL,
                tag_value TEXT NOT NULL,
                confidence REAL,
                source TEXT,
                model_name TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY(question_id) REFERENCES questions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS question_fingerprints (
                question_id INTEGER PRIMARY KEY,
                fingerprint_version INTEGER NOT NULL DEFAULT 1,
                base_fingerprint TEXT NOT NULL,
                style_features_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY(question_id) REFERENCES questions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS question_previews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                preview_type TEXT NOT NULL,
                source_file TEXT,
                page_number INTEGER,
                image_path TEXT,
                bbox_json TEXT,
                status TEXT NOT NULL DEFAULT 'ready',
                message TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY(question_id) REFERENCES questions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS training_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS training_set_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                training_set_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                item_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY(training_set_id) REFERENCES training_sets(id),
                FOREIGN KEY(question_id) REFERENCES questions(id)
            )
            """
        )
        _ensure_columns(
            conn,
            "papers",
            {
                "import_status": "TEXT",
                "content_fingerprint": "TEXT",
                "province": "TEXT",
                "city": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "questions",
            {
                "needs_review": "INTEGER NOT NULL DEFAULT 0",
                "has_images": "INTEGER NOT NULL DEFAULT 0",
                "needs_image_review": "INTEGER NOT NULL DEFAULT 0",
                "reason": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "question_tags",
            {
                "model_name": "TEXT",
            },
        )
        conn.execute(
            """
            UPDATE papers
            SET province = COALESCE(NULLIF(province, ''), '广东省'),
                city = COALESCE(NULLIF(city, ''), '深圳市')
            WHERE title LIKE '%深圳%'
               OR source_file LIKE '%深圳%'
               OR district IN ('深圳市', '福田区', '罗湖区', '南山区', '宝安区', '龙岗区', '龙华区', '盐田区', '坪山区', '光明区', '大鹏新区')
            """
        )
        conn.execute(
            """
            UPDATE papers
            SET semester = CASE
                WHEN title LIKE '%（上）%' OR title LIKE '%(上)%'
                  OR title LIKE '%上学期%' OR title LIKE '%上册%' THEN '上学期'
                WHEN title LIKE '%（下）%' OR title LIKE '%(下)%'
                  OR title LIKE '%下学期%' OR title LIKE '%下册%' THEN '下学期'
                ELSE semester
            END
            WHERE COALESCE(semester, '') = ''
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_number ON questions(question_number)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_paper ON questions(paper_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_type_deleted ON questions(question_type, is_deleted)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_question_tags_question ON question_tags(question_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_question_tags_value ON question_tags(tag_type, tag_value)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_fingerprint ON papers(content_fingerprint)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_source_file ON papers(source_file)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_exam_scope ON papers(exam_type, grade, semester, city)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_question_fingerprints_base ON question_fingerprints(base_fingerprint)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_question_previews_question ON question_previews(question_id, preview_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_training_set_items_set ON training_set_items(training_set_id, item_order)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_training_set_items_question ON training_set_items(question_id)")
        conn.commit()


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing_columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})")
    }
    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
