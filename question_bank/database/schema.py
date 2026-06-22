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
            CREATE TABLE IF NOT EXISTS question_frequency_cache (
                question_id INTEGER PRIMARY KEY,
                score_midterm REAL DEFAULT 0.0,
                score_final REAL DEFAULT 0.0,
                score_zhongkao REAL DEFAULT 0.0,
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
        _create_knowledge_practice_tables(conn)
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
        _ensure_columns(
            conn,
            "knowledge_source_mappings",
            {
                "sub_skill_tags": "TEXT DEFAULT '[]'",
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_freq_midterm ON question_frequency_cache(score_midterm DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_freq_final ON question_frequency_cache(score_final DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_freq_zhongkao ON question_frequency_cache(score_zhongkao DESC)")
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


def _create_knowledge_practice_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            subject TEXT,
            grade TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'archived')),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_concept_id INTEGER NOT NULL,
            target_concept_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL
                CHECK (relation_type IN ('prerequisite', 'related', 'parent')),
            weight REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0 AND weight <= 1.0),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(source_concept_id, target_concept_id, relation_type),
            FOREIGN KEY(source_concept_id) REFERENCES knowledge_concepts(id),
            FOREIGN KEY(target_concept_id) REFERENCES knowledge_concepts(id)
        );

        CREATE TABLE IF NOT EXISTS knowledge_source_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_namespace TEXT NOT NULL,
            source_value TEXT NOT NULL,
            normalized_value TEXT NOT NULL DEFAULT '',
            concept_id INTEGER,
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('confirmed', 'suggested', 'rejected')),
            confidence REAL NOT NULL DEFAULT 0.0
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            sub_skill_tags TEXT DEFAULT '[]',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(source_namespace, source_value),
            FOREIGN KEY(concept_id) REFERENCES knowledge_concepts(id)
        );

        CREATE TABLE IF NOT EXISTS grading_question_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grading_session_id TEXT NOT NULL,
            source_question_id TEXT NOT NULL,
            bank_question_id INTEGER NOT NULL,
            link_method TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('confirmed', 'suggested', 'rejected')),
            evidence_json TEXT NOT NULL DEFAULT '{}',
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(grading_session_id, source_question_id),
            FOREIGN KEY(bank_question_id) REFERENCES questions(id)
        );

        CREATE TABLE IF NOT EXISTS training_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_code TEXT NOT NULL UNIQUE,
            created_by TEXT,
            scope_json TEXT NOT NULL DEFAULT '{}',
            exam_scope_json TEXT NOT NULL DEFAULT '{}',
            diagnosis_snapshot_json TEXT NOT NULL DEFAULT '{}',
            generation_config_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'ready', 'exporting', 'completed', 'cancelled', 'failed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS training_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            variant_key TEXT NOT NULL,
            variant_type TEXT NOT NULL
                CHECK (variant_type IN ('individual', 'group')),
            grouping_reason_json TEXT NOT NULL DEFAULT '{}',
            diagnosis_snapshot_json TEXT NOT NULL DEFAULT '{}',
            shortages_json TEXT NOT NULL DEFAULT '[]',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'ready'
                CHECK (status IN ('ready', 'exporting', 'completed', 'failed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(task_id, variant_key),
            FOREIGN KEY(task_id) REFERENCES training_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS variant_students (
            variant_id INTEGER NOT NULL,
            student_id TEXT NOT NULL,
            student_name_snapshot TEXT,
            class_id_snapshot TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            PRIMARY KEY(variant_id, student_id),
            FOREIGN KEY(variant_id) REFERENCES training_variants(id)
        );

        CREATE TABLE IF NOT EXISTS training_task_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            variant_id INTEGER NOT NULL,
            task_item_code TEXT NOT NULL UNIQUE,
            bank_question_id INTEGER,
            bank_question_fingerprint TEXT,
            item_order INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL
                CHECK (stage IN ('direct', 'prerequisite', 'transfer')),
            concept_snapshot_json TEXT NOT NULL DEFAULT '{}',
            recommendation_snapshot_json TEXT NOT NULL DEFAULT '{}',
            question_snapshot_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(variant_id, item_order),
            FOREIGN KEY(variant_id) REFERENCES training_variants(id),
            FOREIGN KEY(bank_question_id) REFERENCES questions(id)
        );

        CREATE TABLE IF NOT EXISTS training_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            variant_id INTEGER,
            audience TEXT NOT NULL
                CHECK (audience IN ('student', 'teacher', 'bundle')),
            export_format TEXT NOT NULL,
            output_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'succeeded', 'failed')),
            error_message TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY(task_id) REFERENCES training_tasks(id),
            FOREIGN KEY(variant_id) REFERENCES training_variants(id)
        );

        CREATE TABLE IF NOT EXISTS training_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_item_code TEXT NOT NULL,
            student_id TEXT NOT NULL,
            grading_session_id TEXT,
            grading_question_id TEXT,
            score_awarded REAL,
            full_score REAL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(task_item_code, student_id, grading_session_id, grading_question_id),
            FOREIGN KEY(task_item_code) REFERENCES training_task_items(task_item_code)
        );

        CREATE TABLE IF NOT EXISTS skill_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stable_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT 'math',
            grade_min INTEGER,
            grade_max INTEGER,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'archived')),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            CHECK (grade_min IS NULL OR grade_min BETWEEN 1 AND 12),
            CHECK (grade_max IS NULL OR grade_max BETWEEN 1 AND 12),
            CHECK (grade_min IS NULL OR grade_max IS NULL OR grade_min <= grade_max)
        );

        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stable_key TEXT NOT NULL UNIQUE,
            topic_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            grade_min INTEGER,
            grade_max INTEGER,
            origin TEXT NOT NULL CHECK (origin IN ('builtin', 'local')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'merged', 'archived')),
            redirect_skill_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            CHECK (grade_min IS NULL OR grade_min BETWEEN 1 AND 12),
            CHECK (grade_max IS NULL OR grade_max BETWEEN 1 AND 12),
            CHECK (grade_min IS NULL OR grade_max IS NULL OR grade_min <= grade_max),
            CHECK (redirect_skill_id IS NULL OR redirect_skill_id <> id),
            CHECK (
                (status = 'merged' AND redirect_skill_id IS NOT NULL)
                OR (status <> 'merged' AND redirect_skill_id IS NULL)
            ),
            FOREIGN KEY(topic_id) REFERENCES skill_topics(id),
            FOREIGN KEY(redirect_skill_id) REFERENCES skills(id)
        );

        CREATE TABLE IF NOT EXISTS assessment_item_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grading_session_id TEXT NOT NULL,
            source_question_id TEXT NOT NULL,
            skill_id INTEGER,
            role TEXT NOT NULL CHECK (role IN ('measured', 'supporting')),
            raw_knowledge_id TEXT,
            raw_knowledge_label TEXT,
            source TEXT NOT NULL DEFAULT 'resolver',
            confidence REAL NOT NULL DEFAULT 1.0
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            evidence_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'resolved'
                CHECK (status IN ('resolved', 'conflict')),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(grading_session_id, source_question_id, skill_id, role),
            CHECK (
                (status = 'resolved' AND skill_id IS NOT NULL)
                OR (status = 'conflict' AND skill_id IS NULL)
            ),
            FOREIGN KEY(skill_id) REFERENCES skills(id)
        );

        CREATE TABLE IF NOT EXISTS question_skill_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            skill_id INTEGER,
            role TEXT NOT NULL CHECK (role IN ('measured', 'supporting')),
            raw_knowledge_id TEXT,
            raw_knowledge_label TEXT,
            source TEXT NOT NULL DEFAULT 'resolver',
            confidence REAL NOT NULL DEFAULT 1.0
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            evidence_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'resolved'
                CHECK (status IN ('resolved', 'conflict')),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(question_id, skill_id, role),
            CHECK (
                (status = 'resolved' AND skill_id IS NOT NULL)
                OR (status = 'conflict' AND skill_id IS NULL)
            ),
            FOREIGN KEY(question_id) REFERENCES questions(id),
            FOREIGN KEY(skill_id) REFERENCES skills(id)
        );

        CREATE TABLE IF NOT EXISTS skill_resolution_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL
                CHECK (source_type IN ('assessment_item', 'question_bank_item', 'legacy_term')),
            source_ref TEXT NOT NULL,
            raw_label TEXT NOT NULL,
            normalized_label TEXT NOT NULL DEFAULT '',
            candidate_skill_ids_json TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'open'
                CHECK (state IN ('open', 'resolved', 'ignored')),
            resolved_skill_id INTEGER,
            resolved_by TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            CHECK (state <> 'resolved' OR resolved_skill_id IS NOT NULL),
            FOREIGN KEY(resolved_skill_id) REFERENCES skills(id)
        );

        CREATE TABLE IF NOT EXISTS skill_neighbors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_skill_id INTEGER NOT NULL,
            target_skill_id INTEGER NOT NULL,
            kind TEXT NOT NULL
                CHECK (kind IN ('same_topic', 'prerequisite', 'advanced', 'co_assessed')),
            weight REAL NOT NULL DEFAULT 1.0
                CHECK (weight >= 0.0 AND weight <= 1.0),
            source TEXT NOT NULL DEFAULT 'builtin'
                CHECK (source IN ('builtin', 'statistical', 'admin')),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(source_skill_id, target_skill_id, kind),
            CHECK (source_skill_id <> target_skill_id),
            FOREIGN KEY(source_skill_id) REFERENCES skills(id),
            FOREIGN KEY(target_skill_id) REFERENCES skills(id)
        );

        CREATE TABLE IF NOT EXISTS skill_system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS skill_migration_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL UNIQUE,
            mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'apply', 'rollback', 'set_mode')),
            status TEXT NOT NULL
                CHECK (status IN ('running', 'succeeded', 'failed', 'rolled_back')),
            source_counts_json TEXT NOT NULL DEFAULT '{}',
            result_counts_json TEXT NOT NULL DEFAULT '{}',
            invariants_json TEXT NOT NULL DEFAULT '{}',
            report_path TEXT,
            backup_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT,
            started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
            ON knowledge_relations(source_concept_id, relation_type);
        CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
            ON knowledge_relations(target_concept_id, relation_type);
        CREATE INDEX IF NOT EXISTS idx_knowledge_source_mappings_lookup
            ON knowledge_source_mappings(source_namespace, normalized_value, status);
        CREATE INDEX IF NOT EXISTS idx_knowledge_source_mappings_concept
            ON knowledge_source_mappings(concept_id, status);
        CREATE INDEX IF NOT EXISTS idx_grading_question_links_session
            ON grading_question_links(grading_session_id, status);
        CREATE INDEX IF NOT EXISTS idx_grading_question_links_bank_question
            ON grading_question_links(bank_question_id, status);
        CREATE INDEX IF NOT EXISTS idx_training_tasks_status
            ON training_tasks(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_training_variants_task
            ON training_variants(task_id, status);
        CREATE INDEX IF NOT EXISTS idx_variant_students_student
            ON variant_students(student_id, variant_id);
        CREATE INDEX IF NOT EXISTS idx_training_task_items_variant
            ON training_task_items(variant_id, item_order);
        CREATE INDEX IF NOT EXISTS idx_training_task_items_question
            ON training_task_items(bank_question_id);
        CREATE INDEX IF NOT EXISTS idx_training_exports_task
            ON training_exports(task_id, status);
        CREATE INDEX IF NOT EXISTS idx_training_exports_variant
            ON training_exports(variant_id, status);
        CREATE INDEX IF NOT EXISTS idx_training_attempts_student
            ON training_attempts(student_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_skill_topics_status
            ON skill_topics(subject, status, name);
        CREATE INDEX IF NOT EXISTS idx_skills_topic_status
            ON skills(topic_id, status, name);
        CREATE INDEX IF NOT EXISTS idx_skills_name_status
            ON skills(name, status);
        CREATE INDEX IF NOT EXISTS idx_assessment_item_skills_source
            ON assessment_item_skills(grading_session_id, source_question_id, role, status);
        CREATE INDEX IF NOT EXISTS idx_assessment_item_skills_skill
            ON assessment_item_skills(skill_id, role, status);
        CREATE INDEX IF NOT EXISTS idx_question_skill_links_question
            ON question_skill_links(question_id, role, status);
        CREATE INDEX IF NOT EXISTS idx_question_skill_links_skill
            ON question_skill_links(skill_id, role, status);
        CREATE INDEX IF NOT EXISTS idx_skill_conflicts_open
            ON skill_resolution_conflicts(state, source_type, source_ref);
        CREATE INDEX IF NOT EXISTS idx_skill_neighbors_source
            ON skill_neighbors(source_skill_id, enabled, kind, weight);
        CREATE INDEX IF NOT EXISTS idx_skill_neighbors_target
            ON skill_neighbors(target_skill_id, enabled, kind);
        CREATE INDEX IF NOT EXISTS idx_skill_migration_runs_status
            ON skill_migration_runs(status, started_at);

        INSERT OR IGNORE INTO skill_system_settings (key, value)
        VALUES ('recommendation_read_mode', 'legacy');
        INSERT OR IGNORE INTO skill_system_settings (key, value)
        VALUES ('catalog_version', 'junior_math_v1');
        """
    )
