from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ai_grader import GradingResult
from path_manager import resolve_stored_file_path
from scanner import ExamPaperGroup
try:
    from question_bank.taxonomy.registry import canonicalize_knowledge as _registry_canonicalize
except Exception:
    _registry_canonicalize = None  # type: ignore[assignment]


@dataclass
class StudentRecord:
    student_code: str
    name: str
    class_name: str | None = None


class DBManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        try:
            from path_manager import get_path_manager
            pm = get_path_manager()
            if self.db_path.resolve() == pm.db_path.resolve():
                self.backup_dir = pm.backups_dir
            else:
                self.backup_dir = self.db_path.parent / "backups"
        except Exception:
            self.backup_dir = self.db_path.parent / "backups"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        exists = any(row["name"] == column_name for row in cols)
        if not exists:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def create_backup(self, reason: str, *, once_per_day: bool = False) -> Path | None:
        if not self.db_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe_reason = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in reason).strip("_") or "manual"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if once_per_day:
            date_prefix = timestamp[:8]
            existing = sorted(self.backup_dir.glob(f"grading_before_{safe_reason}_{date_prefix}_*.db"))
            if existing:
                return existing[-1]
        backup_path = self.backup_dir / f"grading_before_{safe_reason}_{timestamp}.db"
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        return backup_path

    def initialize(self) -> None:
        with self._connect() as conn:
            # Legacy tables
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exam_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_name TEXT NOT NULL,
                    front_image TEXT NOT NULL,
                    back_image TEXT NOT NULL,
                    total_score REAL NOT NULL,
                    student_score REAL NOT NULL,
                    needs_human_review INTEGER NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS grading_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exam_result_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    score_awarded REAL NOT NULL,
                    deduction_reason TEXT,
                    knowledge_id TEXT NOT NULL,
                    knowledge_ids TEXT,
                    error_category TEXT,
                    error_summary TEXT,
                    FOREIGN KEY(exam_result_id) REFERENCES exam_results(id)
                )
                """
            )
            self._ensure_column(conn, "grading_details", "knowledge_ids", "TEXT")
            self._ensure_column(conn, "grading_details", "error_category", "TEXT")
            self._ensure_column(conn, "grading_details", "error_summary", "TEXT")
            self._ensure_column(conn, "grading_details", "confidence_score", "REAL")

            # Core web tables
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    class_name TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS grading_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_name TEXT NOT NULL,
                    rubric_path TEXT NOT NULL,
                    answer_key_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
                """
            )
            self._ensure_column(conn, "grading_sessions", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "grading_sessions", "deleted_at", "TEXT")
            self._ensure_column(conn, "grading_sessions", "updated_at", "TEXT")
            self._ensure_column(conn, "grading_sessions", "template_config_path", "TEXT")
            conn.execute(
                """
                UPDATE grading_sessions
                SET updated_at = COALESCE(updated_at, datetime('now','localtime'))
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exam_papers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    front_image TEXT NOT NULL,
                    back_image TEXT NOT NULL,
                    ocr_name TEXT,
                    student_id INTEGER,
                    match_status TEXT NOT NULL,
                    processing_status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(student_id) REFERENCES students(id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    paper_id INTEGER NOT NULL,
                    total_score REAL NOT NULL,
                    student_score REAL NOT NULL,
                    needs_human_review INTEGER NOT NULL,
                    raw_json TEXT NOT NULL,
                    graded_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(student_id) REFERENCES students(id),
                    FOREIGN KEY(paper_id) REFERENCES exam_papers(id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    attendance_status TEXT NOT NULL,
                    source_reason TEXT,
                    matched_paper_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    UNIQUE(session_id, student_id),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(student_id) REFERENCES students(id),
                    FOREIGN KEY(matched_paper_id) REFERENCES exam_papers(id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    score_awarded REAL NOT NULL,
                    deduction_reason TEXT,
                    knowledge_id TEXT NOT NULL,
                    knowledge_ids TEXT,
                    error_category TEXT,
                    error_summary TEXT,
                    FOREIGN KEY(result_id) REFERENCES session_results(id)
                )
                """
            )
            self._ensure_column(conn, "session_details", "knowledge_ids", "TEXT")
            self._ensure_column(conn, "session_details", "error_category", "TEXT")
            self._ensure_column(conn, "session_details", "error_summary", "TEXT")
            self._ensure_column(conn, "session_details", "confidence_score", "REAL")

            # Template + annotation tables
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL UNIQUE,
                    front_template_path TEXT NOT NULL,
                    back_template_path TEXT NOT NULL,
                    ai_analysis_path TEXT,
                    template_config_path TEXT,
                    regions_path TEXT,
                    is_confirmed INTEGER NOT NULL DEFAULT 0,
                    regions_snapshot_pending INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id)
                )
                """
            )
            self._ensure_column(conn, "session_templates", "ai_analysis_path", "TEXT")
            self._ensure_column(conn, "session_templates", "template_config_path", "TEXT")
            self._ensure_column(conn, "session_templates", "regions_path", "TEXT")
            self._ensure_column(conn, "session_templates", "regions_snapshot_pending", "INTEGER NOT NULL DEFAULT 0")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS answer_regions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    region_uuid TEXT NOT NULL UNIQUE,
                    session_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    page TEXT NOT NULL,
                    region_order INTEGER NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    w INTEGER NOT NULL,
                    h INTEGER NOT NULL,
                    detected_question_id TEXT,
                    mapped_question_id TEXT,
                    confidence REAL NOT NULL DEFAULT 0,
                    is_confirmed INTEGER NOT NULL DEFAULT 0,
                    mapping_status TEXT NOT NULL DEFAULT 'unbound',
                    multi_region_confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(template_id) REFERENCES session_templates(id)
                )
                """
            )
            self._ensure_column(conn, "answer_regions", "region_uuid", "TEXT")
            self._ensure_column(conn, "answer_regions", "mapping_status", "TEXT NOT NULL DEFAULT 'unbound'")
            self._ensure_column(conn, "answer_regions", "multi_region_confirmed", "INTEGER NOT NULL DEFAULT 0")

            seen_region_uuids: set[str] = set()
            region_identity_rows = conn.execute(
                "SELECT id, region_uuid FROM answer_regions ORDER BY id ASC"
            ).fetchall()
            for row in region_identity_rows:
                region_uuid = row["region_uuid"]
                if region_uuid is not None and str(region_uuid).strip() and str(region_uuid) not in seen_region_uuids:
                    seen_region_uuids.add(str(region_uuid))
                    continue
                replacement_uuid = str(uuid4())
                while replacement_uuid in seen_region_uuids:
                    replacement_uuid = str(uuid4())
                conn.execute(
                    "UPDATE answer_regions SET region_uuid = ? WHERE id = ?",
                    (replacement_uuid, int(row["id"])),
                )
                seen_region_uuids.add(replacement_uuid)

            conn.execute(
                """
                UPDATE answer_regions
                SET mapping_status = CASE
                    WHEN COALESCE(TRIM(mapped_question_id), '') <> '' THEN 'manual'
                    ELSE 'unbound'
                END
                WHERE mapping_status IS NULL
                   OR TRIM(mapping_status) = ''
                   OR (
                       mapping_status = 'unbound'
                       AND COALESCE(TRIM(mapped_question_id), '') <> ''
                   )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_answer_regions_region_uuid_unique ON answer_regions(region_uuid)"
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS answer_regions_region_uuid_required_insert
                BEFORE INSERT ON answer_regions
                WHEN NEW.region_uuid IS NULL OR TRIM(NEW.region_uuid) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'answer_regions.region_uuid must be nonblank');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS answer_regions_region_uuid_required_update
                BEFORE UPDATE OF region_uuid ON answer_regions
                WHEN NEW.region_uuid IS NULL OR TRIM(NEW.region_uuid) = ''
                BEGIN
                    SELECT RAISE(ABORT, 'answer_regions.region_uuid must be nonblank');
                END
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS annotated_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    result_id INTEGER NOT NULL UNIQUE,
                    annotated_front_path TEXT,
                    annotated_back_path TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(session_id) REFERENCES grading_sessions(id),
                    FOREIGN KEY(result_id) REFERENCES session_results(id)
                )
                """
            )

            conn.execute("CREATE INDEX IF NOT EXISTS idx_students_class_name ON students(class_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_grading_sessions_active ON grading_sessions(is_deleted, status, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exam_papers_session_status ON exam_papers(session_id, processing_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exam_papers_student ON exam_papers(student_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_results_session_student ON session_results(session_id, student_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_results_paper ON session_results(paper_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_attendance_session_status ON session_attendance(session_id, attendance_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_details_result ON session_details(result_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_details_question ON session_details(question_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_grading_details_exam_result ON grading_details(exam_result_id)")

            conn.commit()

    # ---------- Legacy API ----------
    def save_result(self, paper_group: ExamPaperGroup, grading_result: GradingResult) -> int:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO exam_results (
                    student_name,
                    front_image,
                    back_image,
                    total_score,
                    student_score,
                    needs_human_review,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grading_result.student_name,
                    str(paper_group.front_image),
                    str(paper_group.back_image),
                    grading_result.total_score,
                    grading_result.student_score,
                    1 if grading_result.needs_human_review else 0,
                    json.dumps(grading_result.raw_json, ensure_ascii=False),
                ),
            )

            exam_result_id = int(cursor.lastrowid)
            for detail in grading_result.grading_details:
                cursor.execute(
                    """
                    INSERT INTO grading_details (
                        exam_result_id,
                        question_id,
                        score_awarded,
                        deduction_reason,
                        knowledge_id,
                        knowledge_ids
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        exam_result_id,
                        detail.question_id,
                        detail.score_awarded,
                        detail.deduction_reason,
                        detail.knowledge_id,
                        json.dumps(_detail_knowledge_ids(detail), ensure_ascii=False),
                    ),
                )
            conn.commit()
            return exam_result_id

    # ---------- Student ----------
    def upsert_students(self, students: list[StudentRecord]) -> dict[str, int]:
        if not students:
            return {"inserted": 0, "updated": 0, "total": 0}
            
        if hasattr(self, "_cached_students_for_find"):
            delattr(self, "_cached_students_for_find")

        inserted = 0
        updated = 0

        with self._connect() as conn:
            cursor = conn.cursor()
            
            codes = [s.student_code for s in students]
            placeholders = ",".join(["?"] * len(codes))
            existing_rows = cursor.execute(
                f"SELECT id, student_code, name, class_name FROM students WHERE student_code IN ({placeholders})",
                codes
            ).fetchall()
            existing_map = {row["student_code"]: row for row in existing_rows}
            
            to_insert = []
            to_update = []
            
            for s in students:
                existing = existing_map.get(s.student_code)
                if not existing:
                    to_insert.append((s.student_code, s.name, s.class_name))
                else:
                    if existing["name"] != s.name or (existing["class_name"] or "") != (s.class_name or ""):
                        to_update.append((s.name, s.class_name, existing["id"]))

            if to_insert:
                cursor.executemany("INSERT INTO students (student_code, name, class_name) VALUES (?, ?, ?)", to_insert)
                inserted = len(to_insert)
            if to_update:
                cursor.executemany("UPDATE students SET name = ?, class_name = ? WHERE id = ?", to_update)
                updated = len(to_update)
            conn.commit()

        return {"inserted": inserted, "updated": updated, "total": inserted + updated}

    def list_students(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, student_code, name, class_name, created_at FROM students ORDER BY class_name ASC, name ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    def update_student(self, student_id: int, student_code: str, name: str, class_name: str | None) -> None:
        if hasattr(self, "_cached_students_for_find"):
            delattr(self, "_cached_students_for_find")
        clean_code = str(student_code or "").strip()
        clean_name = str(name or "").strip()
        clean_class_name = str(class_name or "").strip() or None
        if not clean_code:
            raise ValueError("学生学号不能为空")
        if not clean_name:
            raise ValueError("学生姓名不能为空")

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE students
                    SET student_code = ?, name = ?, class_name = ?
                    WHERE id = ?
                    """,
                    (clean_code, clean_name, clean_class_name, int(student_id)),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"未找到学生记录: {student_id}")
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"学号已存在，无法保存: {clean_code}") from exc

    def delete_student_hard(self, student_id: int) -> dict[str, int]:
        if hasattr(self, "_cached_students_for_find"):
            delattr(self, "_cached_students_for_find")
        self.create_backup("delete_student")
        with self._connect() as conn:
            result_rows = conn.execute(
                "SELECT id FROM session_results WHERE student_id = ?",
                (int(student_id),),
            ).fetchall()
            result_ids = [int(row["id"]) for row in result_rows]
            deleted_details = 0
            deleted_annotations = 0
            if result_ids:
                placeholders = ",".join(["?"] * len(result_ids))
                deleted_details = conn.execute(
                    f"DELETE FROM session_details WHERE result_id IN ({placeholders})",
                    result_ids,
                ).rowcount
                deleted_annotations = conn.execute(
                    f"DELETE FROM annotated_results WHERE result_id IN ({placeholders})",
                    result_ids,
                ).rowcount

            deleted_attendance = conn.execute(
                "DELETE FROM session_attendance WHERE student_id = ?",
                (int(student_id),),
            ).rowcount
            deleted_results = conn.execute(
                "DELETE FROM session_results WHERE student_id = ?",
                (int(student_id),),
            ).rowcount
            unlinked_papers = conn.execute(
                """
                UPDATE exam_papers
                SET student_id = NULL, match_status = 'student_deleted'
                WHERE student_id = ?
                """,
                (int(student_id),),
            ).rowcount
            deleted_students = conn.execute(
                "DELETE FROM students WHERE id = ?",
                (int(student_id),),
            ).rowcount
            conn.commit()
            return {
                "deleted_students": int(deleted_students),
                "deleted_results": int(deleted_results),
                "deleted_details": int(deleted_details),
                "deleted_annotations": int(deleted_annotations),
                "deleted_attendance": int(deleted_attendance),
                "unlinked_papers": int(unlinked_papers),
            }

    def find_student_by_name(self, name: str) -> dict[str, Any] | None:
        normalized_target = _normalize_name(name)
        if not normalized_target:
            return None

        if not hasattr(self, "_cached_students_for_find"):
            with self._connect() as conn:
                self._cached_students_for_find = [dict(row) for row in conn.execute("SELECT id, student_code, name, class_name FROM students").fetchall()]

        for row in self._cached_students_for_find:
            if _normalize_name(row["name"]) == normalized_target:
                return row
        return None

    # ---------- Session ----------
    def set_app_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, datetime('now','localtime'))
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = datetime('now','localtime')
                """,
                (str(key), str(value)),
            )
            conn.commit()

    def get_app_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?",
                (str(key),),
            ).fetchone()
        return str(row["setting_value"]) if row else default

    def create_grading_session(self, session_name: str, rubric_path: str, answer_key_path: str) -> int:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO grading_sessions (session_name, rubric_path, answer_key_path, status, is_deleted, updated_at)
                VALUES (?, ?, ?, 'created', 0, datetime('now','localtime'))
                """,
                (session_name, rubric_path, answer_key_path),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def rename_grading_session(self, session_id: int, new_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE grading_sessions
                SET session_name = ?, updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (new_name, session_id),
            )
            conn.commit()

    def update_grading_session_config(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        template_config_path: str | None = None,
    ) -> None:
        if hasattr(self, "_rubric_map_cache"):
            self._rubric_map_cache.pop(session_id, None)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE grading_sessions
                SET rubric_path = ?,
                    answer_key_path = ?,
                    template_config_path = COALESCE(?, template_config_path),
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (rubric_path, answer_key_path, template_config_path, session_id),
            )
            conn.commit()

    def soft_delete_grading_session(self, session_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE grading_sessions
                SET is_deleted = 1,
                    deleted_at = datetime('now','localtime'),
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (session_id,),
            )
            conn.commit()

    def restore_grading_session(self, session_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE grading_sessions
                SET is_deleted = 0,
                    deleted_at = NULL,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (session_id,),
            )
            conn.commit()

    def list_grading_sessions(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT id, session_name, rubric_path, answer_key_path, template_config_path,
                   status, is_deleted, deleted_at, created_at, updated_at
            FROM grading_sessions
        """
        if not include_deleted:
            query += " WHERE is_deleted = 0"
        query += " ORDER BY id DESC"

        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def get_grading_session(self, session_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, session_name, rubric_path, answer_key_path, template_config_path,
                       status, is_deleted, deleted_at, created_at, updated_at
                FROM grading_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def collect_session_storage_paths(self, session_id: int) -> list[str]:
        paths: list[str] = []

        def add_values(row: sqlite3.Row | None, fields: list[str]) -> None:
            if row is None:
                return
            for field in fields:
                value = row[field]
                if value:
                    paths.append(str(value))

        with self._connect() as conn:
            add_values(
                conn.execute(
                    """
                    SELECT rubric_path, answer_key_path, template_config_path
                    FROM grading_sessions
                    WHERE id = ?
                    """,
                    (session_id,),
                ).fetchone(),
                ["rubric_path", "answer_key_path", "template_config_path"],
            )
            add_values(
                conn.execute(
                    """
                    SELECT front_template_path, back_template_path, ai_analysis_path,
                           template_config_path, regions_path
                    FROM session_templates
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone(),
                [
                    "front_template_path",
                    "back_template_path",
                    "ai_analysis_path",
                    "template_config_path",
                    "regions_path",
                ],
            )
            rows = conn.execute(
                """
                SELECT front_image, back_image
                FROM exam_papers
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
            for row in rows:
                add_values(row, ["front_image", "back_image"])

            rows = conn.execute(
                """
                SELECT annotated_front_path, annotated_back_path
                FROM annotated_results
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
            for row in rows:
                add_values(row, ["annotated_front_path", "annotated_back_path"])

        return paths

    def hard_delete_grading_session(self, session_id: int) -> dict[str, int]:
        session = self.get_grading_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} does not exist.")
        if int(session.get("is_deleted") or 0) != 1:
            raise ValueError("Only sessions in recycle bin can be permanently deleted.")

        counts = {
            "session_details": 0,
            "annotated_results": 0,
            "session_attendance": 0,
            "session_results": 0,
            "exam_papers": 0,
            "answer_regions": 0,
            "session_templates": 0,
            "grading_sessions": 0,
        }

        with self._connect() as conn:
            result_rows = conn.execute("SELECT id FROM session_results WHERE session_id = ?", (session_id,)).fetchall()
            result_ids = [int(row["id"]) for row in result_rows]

            if result_ids:
                placeholders = ",".join(["?"] * len(result_ids))
                counts["session_details"] += int(
                    conn.execute(f"DELETE FROM session_details WHERE result_id IN ({placeholders})", result_ids).rowcount
                    or 0
                )
                counts["annotated_results"] += int(
                    conn.execute(f"DELETE FROM annotated_results WHERE result_id IN ({placeholders})", result_ids).rowcount
                    or 0
                )

            counts["annotated_results"] += int(
                conn.execute("DELETE FROM annotated_results WHERE session_id = ?", (session_id,)).rowcount or 0
            )
            counts["session_attendance"] += int(
                conn.execute("DELETE FROM session_attendance WHERE session_id = ?", (session_id,)).rowcount or 0
            )
            counts["session_results"] += int(
                conn.execute("DELETE FROM session_results WHERE session_id = ?", (session_id,)).rowcount or 0
            )
            counts["exam_papers"] += int(
                conn.execute("DELETE FROM exam_papers WHERE session_id = ?", (session_id,)).rowcount or 0
            )
            counts["answer_regions"] += int(
                conn.execute("DELETE FROM answer_regions WHERE session_id = ?", (session_id,)).rowcount or 0
            )
            counts["session_templates"] += int(
                conn.execute("DELETE FROM session_templates WHERE session_id = ?", (session_id,)).rowcount or 0
            )
            counts["grading_sessions"] += int(
                conn.execute(
                    "DELETE FROM grading_sessions WHERE id = ? AND is_deleted = 1",
                    (session_id,),
                ).rowcount
                or 0
            )
            conn.commit()

        return counts

    def update_session_status(self, session_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE grading_sessions SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (status, session_id),
            )
            conn.commit()

    def try_start_session_run(self, session_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE grading_sessions
                SET status = 'running', updated_at = datetime('now','localtime')
                WHERE id = ? AND COALESCE(status, '') <> 'running'
                """,
                (session_id,),
            )
            conn.commit()
            return int(cursor.rowcount or 0) == 1

    def finish_session_run(self, session_id: int, status: str = "completed") -> None:
        self.update_session_status(session_id, status)

    def clear_session_run_data(self, session_id: int) -> None:
        self.create_backup("clear_session")
        with self._connect() as conn:
            result_rows = conn.execute("SELECT id FROM session_results WHERE session_id = ?", (session_id,)).fetchall()
            result_ids = [row["id"] for row in result_rows]

            if result_ids:
                placeholders = ",".join(["?"] * len(result_ids))
                conn.execute(f"DELETE FROM session_details WHERE result_id IN ({placeholders})", result_ids)
                conn.execute(f"DELETE FROM annotated_results WHERE result_id IN ({placeholders})", result_ids)

            conn.execute("DELETE FROM session_attendance WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_results WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM exam_papers WHERE session_id = ?", (session_id,))
            conn.commit()

    def replace_session_attendance(self, session_id: int, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM session_attendance WHERE session_id = ?", (session_id,))
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO session_attendance (
                        session_id, student_id, attendance_status, source_reason, matched_paper_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        int(row["student_id"]),
                        str(row["attendance_status"]),
                        row.get("source_reason"),
                        row.get("matched_paper_id"),
                    ),
                )
            conn.commit()

    def get_session_attendance(self, session_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    sa.id,
                    sa.session_id,
                    sa.student_id,
                    s.student_code,
                    s.name AS student_name,
                    s.class_name,
                    sa.attendance_status,
                    sa.source_reason,
                    sa.matched_paper_id,
                    sa.created_at
                FROM session_attendance sa
                JOIN students s ON s.id = sa.student_id
                WHERE sa.session_id = ?
                ORDER BY s.class_name ASC, s.student_code ASC, s.name ASC
                """,
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ---------- Template & regions ----------
    def upsert_session_template(self, session_id: int, front_template_path: str, back_template_path: str) -> int:
        with self._connect() as conn:
            cursor = conn.cursor()
            existing = cursor.execute(
                "SELECT id FROM session_templates WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            if existing:
                template_id = int(existing["id"])
                cursor.execute(
                    """
                    UPDATE session_templates
                    SET front_template_path = ?, back_template_path = ?, is_confirmed = 0,
                        updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (front_template_path, back_template_path, template_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO session_templates (session_id, front_template_path, back_template_path, is_confirmed)
                    VALUES (?, ?, ?, 0)
                    """,
                    (session_id, front_template_path, back_template_path),
                )
                template_id = int(cursor.lastrowid)

            conn.commit()
            return template_id

    def get_session_template(self, session_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, front_template_path, back_template_path,
                       ai_analysis_path, template_config_path, regions_path,
                       is_confirmed, regions_snapshot_pending, created_at, updated_at
                FROM session_templates
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_session_template_analysis(
        self,
        session_id: int,
        *,
        ai_analysis_path: str | None,
        template_config_path: str | None,
        regions_path: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_templates
                SET ai_analysis_path = ?,
                    template_config_path = ?,
                    regions_path = ?,
                    is_confirmed = 0,
                    updated_at = datetime('now','localtime')
                WHERE session_id = ?
                """,
                (ai_analysis_path, template_config_path, regions_path, session_id),
            )
            conn.commit()

    def _insert_answer_region_conn(
        self,
        conn: sqlite3.Connection,
        session_id: int,
        template_id: int,
        region: dict[str, Any],
    ) -> int:
        raw_region_uuid = region.get("region_uuid")
        region_uuid = (
            str(raw_region_uuid)
            if raw_region_uuid is not None and str(raw_region_uuid).strip()
            else str(uuid4())
        )
        mapped_question_id = region.get("mapped_question_id")
        raw_mapping_status = region.get("mapping_status")
        mapping_status = (
            str(raw_mapping_status)
            if raw_mapping_status is not None and str(raw_mapping_status).strip()
            else ("manual" if mapped_question_id is not None and str(mapped_question_id).strip() else "unbound")
        )
        cursor = conn.execute(
            """
            INSERT INTO answer_regions (
                region_uuid, session_id, template_id, page, region_order, x, y, w, h,
                detected_question_id, mapped_question_id, confidence, is_confirmed,
                mapping_status, multi_region_confirmed, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
            """,
            (
                region_uuid,
                session_id,
                template_id,
                region.get("page", "front"),
                int(region.get("region_order", 0)),
                int(region.get("x", 0)),
                int(region.get("y", 0)),
                int(region.get("w", 0)),
                int(region.get("h", 0)),
                region.get("detected_question_id"),
                mapped_question_id,
                float(region.get("confidence", 0.0)),
                1 if region.get("is_confirmed") else 0,
                mapping_status,
                1 if region.get("multi_region_confirmed") else 0,
            ),
        )
        return int(cursor.lastrowid)

    def save_answer_regions(self, session_id: int, template_id: int, regions: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM answer_regions WHERE session_id = ?", (session_id,))
            for region in regions:
                self._insert_answer_region_conn(conn, session_id, template_id, region)
            conn.commit()

    def list_answer_regions(self, session_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, region_uuid, session_id, template_id, page, region_order, x, y, w, h,
                       detected_question_id, mapped_question_id, confidence, is_confirmed,
                       mapping_status, multi_region_confirmed, created_at, updated_at
                FROM answer_regions
                WHERE session_id = ?
                ORDER BY CASE page WHEN 'front' THEN 1 ELSE 2 END, region_order ASC
                """,
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def bulk_update_answer_region_mapping(self, session_id: int, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            for row in rows:
                mapped_question_id = row.get("mapped_question_id")
                mapping_status = row.get("mapping_status")
                if mapping_status not in {"auto", "manual", "unbound"}:
                    mapping_status = (
                        "manual"
                        if mapped_question_id is not None and str(mapped_question_id).strip()
                        else "unbound"
                    )
                conn.execute(
                    """
                    UPDATE answer_regions
                    SET mapped_question_id = ?,
                        mapping_status = ?,
                        is_confirmed = ?,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND session_id = ?
                    """,
                    (
                        mapped_question_id,
                        mapping_status,
                        1 if row.get("is_confirmed") else 0,
                        int(row.get("id")),
                        session_id,
                    ),
                )
            conn.commit()

    def add_answer_region(self, session_id: int, template_id: int, region: dict[str, Any]) -> int:
        """Insert a single region and return its new id."""
        with self._connect() as conn:
            region_id = self._insert_answer_region_conn(conn, session_id, template_id, region)
            conn.commit()
            return region_id

    def replace_answer_regions_atomic(
        self,
        session_id: int,
        template_id: int,
        regions: list[dict[str, Any]],
        *,
        confirmed: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM answer_regions WHERE session_id = ?", (session_id,))
            for region in regions:
                self._insert_answer_region_conn(conn, session_id, template_id, region)
            conn.execute(
                """
                UPDATE session_templates
                SET is_confirmed = ?,
                    regions_snapshot_pending = 1,
                    updated_at = datetime('now','localtime')
                WHERE session_id = ?
                """,
                (1 if confirmed else 0, session_id),
            )
            conn.commit()

    def mark_region_snapshot_complete(self, session_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_templates
                SET regions_snapshot_pending = 0,
                    updated_at = datetime('now','localtime')
                WHERE session_id = ?
                """,
                (session_id,),
            )
            conn.commit()

    def delete_answer_region(self, region_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM answer_regions WHERE id = ?", (region_id,))
            conn.commit()

    def update_answer_region_bbox(
        self,
        region_id: int,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE answer_regions
                SET x = ?, y = ?, w = ?, h = ?, updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (x, y, w, h, region_id),
            )
            conn.commit()

    def mark_template_confirmed(self, session_id: int, confirmed: bool = True) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_templates
                SET is_confirmed = ?, updated_at = datetime('now','localtime')
                WHERE session_id = ?
                """,
                (1 if confirmed else 0, session_id),
            )
            conn.commit()

    def is_template_ready(self, session_id: int) -> bool:
        with self._connect() as conn:
            tpl = conn.execute(
                "SELECT id, is_confirmed FROM session_templates WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if tpl is None or int(tpl["is_confirmed"]) != 1:
                return False

            total = conn.execute(
                "SELECT COUNT(*) AS c FROM answer_regions WHERE session_id = ?",
                (session_id,),
            ).fetchone()["c"]
            confirmed = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM answer_regions
                WHERE session_id = ?
                  AND is_confirmed = 1
                  AND COALESCE(mapped_question_id, '') <> ''
                """,
                (session_id,),
            ).fetchone()["c"]

            return total > 0 and total == confirmed

    # ---------- Paper/result persistence ----------
    def create_exam_paper(
        self,
        session_id: int,
        front_image: str,
        back_image: str,
        ocr_name: str,
        student_id: int | None,
        match_status: str,
        processing_status: str,
        error_message: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    front_image,
                    back_image,
                    ocr_name,
                    student_id,
                    match_status,
                    processing_status,
                    error_message,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_exam_paper_status(self, paper_id: int, processing_status: str, error_message: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE exam_papers SET processing_status = ?, error_message = ? WHERE id = ?",
                (processing_status, error_message, paper_id),
            )
            conn.commit()

    def save_session_result(self, session_id: int, student_id: int, paper_id: int, grading_result: GradingResult) -> int:
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Remove old results for this student in this session
            cursor.execute("SELECT id FROM session_results WHERE session_id = ? AND student_id = ?", (session_id, student_id))
            old_rows = cursor.fetchall()
            for old_row in old_rows:
                old_id = old_row[0]
                cursor.execute("DELETE FROM session_details WHERE result_id = ?", (old_id,))
                cursor.execute("DELETE FROM session_results WHERE id = ?", (old_id,))
                
            cursor.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    student_id,
                    paper_id,
                    grading_result.total_score,
                    grading_result.student_score,
                    1 if grading_result.needs_human_review else 0,
                    json.dumps(grading_result.raw_json, ensure_ascii=False),
                ),
            )

            result_id = int(cursor.lastrowid)
            for detail in grading_result.grading_details:
                cursor.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded, deduction_reason,
                        knowledge_id, knowledge_ids, error_category, error_summary, confidence_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_id,
                        detail.question_id,
                        detail.score_awarded,
                        detail.deduction_reason,
                        detail.knowledge_id,
                        json.dumps(_detail_knowledge_ids(detail), ensure_ascii=False),
                        getattr(detail, "error_category", None),
                        getattr(detail, "error_summary", None),
                        getattr(detail, "confidence_score", None),
                    ),
                )
            conn.commit()
            return result_id

    def get_session_progress(self, session_id: int) -> dict[str, int | float]:
        with self._connect() as conn:
            counts = conn.execute("""
                SELECT 
                    COUNT(*) AS total,
                    SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END) AS matched,
                    SUM(CASE WHEN match_status <> 'matched' THEN 1 ELSE 0 END) AS unmatched,
                    SUM(CASE WHEN processing_status = 'graded' THEN 1 ELSE 0 END) AS graded,
                    SUM(CASE WHEN processing_status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN processing_status = 'grading' THEN 1 ELSE 0 END) AS in_progress
                FROM exam_papers WHERE session_id = ?
            """, (session_id,)).fetchone()
            
            review_count = conn.execute(
                "SELECT COUNT(*) AS c FROM session_results WHERE session_id = ? AND needs_human_review = 1",
                (session_id,)
            ).fetchone()["c"]
            
            att = conn.execute("""
                SELECT 
                    SUM(CASE WHEN attendance_status = 'absent' THEN 1 ELSE 0 END) AS absent,
                    SUM(CASE WHEN attendance_status = 'scan_issue' THEN 1 ELSE 0 END) AS scan_issue
                FROM session_attendance WHERE session_id = ?
            """, (session_id,)).fetchone()

        total = counts["total"] or 0
        matched = counts["matched"] or 0
        unmatched = counts["unmatched"] or 0
        graded = counts["graded"] or 0
        failed = counts["failed"] or 0
        in_progress = counts["in_progress"] or 0
        absent = att["absent"] or 0
        scan_issue = att["scan_issue"] or 0

        done = graded + failed
        progress_percent = round((done / matched) * 100, 2) if matched else 0.0
        return {
            "total_papers": int(total),
            "matched_papers": int(matched),
            "unmatched_papers": int(unmatched),
            "graded_papers": int(graded),
            "failed_papers": int(failed),
            "grading_papers": int(in_progress),
            "needs_human_review": int(review_count),
            "absent_students": int(absent),
            "scan_issue_students": int(scan_issue),
            "progress_percent": progress_percent,
        }

    def list_failed_papers(self, session_id: int) -> list[dict[str, Any]]:
        """返回本场次中批改失败（processing_status='failed'）的所有试卷，含学生姓名与错误信息。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    ep.id AS paper_id,
                    ep.ocr_name,
                    COALESCE(s.name, ep.ocr_name, '未知') AS student_name,
                    s.student_code,
                    s.class_name,
                    ep.error_message,
                    ep.created_at
                FROM exam_papers ep
                LEFT JOIN students s ON s.id = ep.student_id
                WHERE ep.session_id = ? AND ep.processing_status = 'failed'
                ORDER BY ep.id ASC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_failed_papers_detailed(self, session_id: int) -> list[dict[str, Any]]:
        """返回本场次中批改失败（processing_status='failed'）的所有试卷的详细信息，用于增量重试。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id AS paper_id,
                    front_image,
                    back_image,
                    ocr_name,
                    student_id,
                    match_status
                FROM exam_papers
                WHERE session_id = ? AND processing_status = 'failed'
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session_results(self, session_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    sr.id AS result_id,
                    s.student_code,
                    s.name AS student_name,
                    s.class_name,
                    ep.ocr_name,
                    ep.front_image,
                    ep.back_image,
                    sr.total_score,
                    sr.student_score,
                    sr.needs_human_review,
                    sr.graded_at,
                    sr.raw_json
                FROM session_results sr
                JOIN students s ON s.id = sr.student_id
                JOIN exam_papers ep ON ep.id = sr.paper_id
                WHERE sr.session_id = ?
                ORDER BY sr.id ASC
                """,
                (session_id,),
            ).fetchall()

        results = [dict(row) for row in rows]
        for item in results:
            item["raw_json"] = _safe_json_loads(item.get("raw_json"))
        return results

    def get_result_context(self, result_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    sr.id AS result_id,
                    sr.session_id,
                    sr.student_id,
                    sr.paper_id,
                    ep.front_image,
                    ep.back_image
                FROM session_results sr
                JOIN exam_papers ep ON ep.id = sr.paper_id
                WHERE sr.id = ?
                """,
                (result_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_result_details(self, result_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id AS detail_id,
                    question_id,
                    score_awarded,
                    deduction_reason,
                    knowledge_id,
                    knowledge_ids,
                    error_category,
                    error_summary,
                    confidence_score
                FROM session_details
                WHERE result_id = ?
                ORDER BY id ASC
                """,
                (result_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_result_detail(
        self,
        detail_id: int,
        score_awarded: float,
        deduction_reason: str | None,
        error_category: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_details
                SET score_awarded = ?, deduction_reason = ?, error_category = ?, error_summary = ?
                WHERE id = ?
                """,
                (float(score_awarded), deduction_reason, error_category, error_summary, detail_id),
            )
            conn.commit()

    def recalculate_result_score(self, result_id: int) -> None:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COALESCE(SUM(score_awarded),0) AS s FROM session_details WHERE result_id = ?",
                (result_id,),
            ).fetchone()["s"]
            conn.execute(
                "UPDATE session_results SET student_score = ? WHERE id = ?",
                (float(total), result_id),
            )
            conn.commit()

    def update_session_detail_scores(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        if not adjustments:
            return {"updated_details": 0, "updated_results": 0}

        detail_ids = [int(item["detail_id"]) for item in adjustments]
        if len(detail_ids) != len(set(detail_ids)):
            raise ValueError("同一评分明细不能重复调整。")

        placeholders = ",".join(["?"] * len(detail_ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sd.id AS detail_id, sd.result_id
                FROM session_details sd
                JOIN session_results sr ON sr.id = sd.result_id
                WHERE sr.session_id = ? AND sd.id IN ({placeholders})
                """,
                [int(session_id), *detail_ids],
            ).fetchall()
            result_by_detail = {int(row["detail_id"]): int(row["result_id"]) for row in rows}
            missing_ids = [detail_id for detail_id in detail_ids if detail_id not in result_by_detail]
            if missing_ids:
                raise ValueError(f"评分明细不属于当前考试：{missing_ids}")

            for item in adjustments:
                conn.execute(
                    "UPDATE session_details SET score_awarded = ? WHERE id = ?",
                    (float(item["score_awarded"]), int(item["detail_id"])),
                )

            result_ids = sorted(set(result_by_detail.values()))
            for result_id in result_ids:
                total = conn.execute(
                    "SELECT COALESCE(SUM(score_awarded),0) AS s FROM session_details WHERE result_id = ?",
                    (result_id,),
                ).fetchone()["s"]
                conn.execute(
                    "UPDATE session_results SET student_score = ? WHERE id = ?",
                    (float(total), result_id),
                )
            conn.commit()

        return {"updated_details": len(adjustments), "updated_results": len(result_ids)}

    def upsert_annotated_result(self, session_id: int, result_id: int, annotated_front_path: str, annotated_back_path: str) -> None:
        with self._connect() as conn:
            existing = conn.execute("SELECT id FROM annotated_results WHERE result_id = ?", (result_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE annotated_results
                    SET annotated_front_path = ?, annotated_back_path = ?, updated_at = datetime('now','localtime')
                    WHERE result_id = ?
                    """,
                    (annotated_front_path, annotated_back_path, result_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO annotated_results (session_id, result_id, annotated_front_path, annotated_back_path)
                    VALUES (?, ?, ?, ?)
                    """,
                    (session_id, result_id, annotated_front_path, annotated_back_path),
                )
            conn.commit()

    def get_annotated_result(self, result_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, result_id, annotated_front_path, annotated_back_path, updated_at
                FROM annotated_results
                WHERE result_id = ?
                """,
                (result_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_session_weak_points(self, session_id: int, student_id: int | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT
                sr.session_id,
                s.id AS student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sd.question_id,
                    sd.knowledge_id,
                    sd.knowledge_ids,
                    sd.score_awarded,
                    sd.deduction_reason,
                    sd.error_category,
                    sd.error_summary
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN students s ON s.id = sr.student_id
            WHERE sr.session_id = ?
        """
        params: list[Any] = [session_id]
        if student_id is not None:
            query += " AND s.id = ?"
            params.append(student_id)

        query += " ORDER BY s.id ASC, sd.knowledge_id ASC, sd.id ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return self._build_weak_point_rows([dict(row) for row in rows])

    def get_active_global_weak_points(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                sr.session_id,
                s.id AS student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sd.question_id,
                    sd.knowledge_id,
                    sd.knowledge_ids,
                    sd.score_awarded,
                    sd.deduction_reason,
                    sd.error_category,
                    sd.error_summary
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN grading_sessions gs ON gs.id = sr.session_id
            JOIN students s ON s.id = sr.student_id
            WHERE COALESCE(gs.is_deleted, 0) = 0
        """
        params: list[Any] = []
        if student_id is not None:
            query += " AND s.id = ?"
            params.append(student_id)
        if session_ids:
            placeholders = ",".join(["?"] * len(session_ids))
            query += f" AND sr.session_id IN ({placeholders})"
            params.extend(int(value) for value in session_ids)

        query += " ORDER BY s.id ASC, sd.knowledge_id ASC, sd.id ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return self._build_weak_point_rows([dict(row) for row in rows])

    def get_active_global_error_points(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                sr.session_id,
                s.id AS student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sd.question_id,
                sd.score_awarded,
                sd.deduction_reason,
                sd.error_category,
                sd.error_summary
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN grading_sessions gs ON gs.id = sr.session_id
            JOIN students s ON s.id = sr.student_id
            WHERE COALESCE(gs.is_deleted, 0) = 0
        """
        params: list[Any] = []
        if student_id is not None:
            query += " AND s.id = ?"
            params.append(student_id)
        if session_ids:
            placeholders = ",".join(["?"] * len(session_ids))
            query += f" AND sr.session_id IN ({placeholders})"
            params.extend(int(value) for value in session_ids)
        query += " ORDER BY s.id ASC, sd.id ASC"

        with self._connect() as conn:
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        return self._build_error_point_rows(rows)

    def get_active_student_score_rates(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.id AS student_id,
                    s.student_code,
                    s.name AS student_name,
                    s.class_name,
                    ROUND(AVG(CASE WHEN sr.total_score > 0 THEN sr.student_score * 100.0 / sr.total_score ELSE 0 END), 2) AS avg_score_rate,
                    COUNT(DISTINCT sr.session_id) AS exam_count
                FROM session_results sr
                JOIN grading_sessions gs ON gs.id = sr.session_id
                JOIN students s ON s.id = sr.student_id
                WHERE COALESCE(gs.is_deleted, 0) = 0
                GROUP BY s.id, s.student_code, s.name, s.class_name
                ORDER BY s.student_code ASC, s.name ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_active_wrong_items_for_knowledge(self, student_id: int, knowledge_id: str) -> list[dict[str, Any]]:
        return [
            item for item in self.get_active_items_for_knowledge(student_id, knowledge_id)
            if item.get("is_deducted")
        ]

    def get_active_items_for_knowledge(self, student_id: int, knowledge_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    sr.session_id,
                    gs.session_name,
                    sr.id AS result_id,
                    sr.student_id,
                    s.student_code,
                    s.name AS student_name,
                    ep.front_image,
                    ep.back_image,
                    sd.question_id,
                    sd.knowledge_id,
                    sd.knowledge_ids,
                    sd.score_awarded,
                    sd.deduction_reason,
                    sd.error_category,
                    sd.error_summary,
                    sr.graded_at
                FROM session_details sd
                JOIN session_results sr ON sr.id = sd.result_id
                JOIN grading_sessions gs ON gs.id = sr.session_id
                JOIN students s ON s.id = sr.student_id
                JOIN exam_papers ep ON ep.id = sr.paper_id
                WHERE COALESCE(gs.is_deleted, 0) = 0
                  AND sr.student_id = ?
                ORDER BY sr.graded_at DESC, sd.id ASC
                """,
                (student_id,),
            ).fetchall()

        result: list[dict[str, Any]] = []
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}
        for row in [dict(item) for item in rows]:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            qid = str(row.get("question_id") or "")
            row_knowledge_ids = rubric_cache[session_id].get("knowledge", {}).get(qid) or _knowledge_ids_from_row(row)
            if not _knowledge_id_matches(knowledge_id, row_knowledge_ids):
                continue
            full_score = rubric_cache[session_id]["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            reason = str(row.get("deduction_reason") or "")
            max_score = full_score if full_score is not None else awarded
            row["max_score"] = max_score
            row["score_rate"] = round(awarded / max_score * 100, 2) if max_score else 0.0
            row["is_deducted"] = _is_deducted(awarded, full_score, reason)
            row["deduction_amount"] = max(0.0, round((max_score or 0.0) - awarded, 2))
            row["knowledge_label"] = rubric_cache[session_id]["label"].get(
                knowledge_id,
                _fallback_knowledge_label(knowledge_id, qid),
            )
            result.append(row)
        return sorted(
            result,
            key=lambda item: (
                float(item.get("score_rate") or 0),
                0 if item.get("is_deducted") else 1,
                str(item.get("graded_at") or ""),
            ),
        )

    def get_representative_wrong_items_for_knowledge(
        self,
        knowledge_id: str,
        session_ids: list[int] | None = None,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        rows = self._query_active_detail_rows(session_ids=session_ids)
        result = self._enrich_detail_rows(rows, knowledge_id=knowledge_id)
        wrong_items = [item for item in result if item.get("is_deducted")]
        random.shuffle(wrong_items)
        return sorted(
            wrong_items,
            key=lambda item: (
                float(item.get("score_rate") or 0),
                str(item.get("graded_at") or ""),
            ),
        )[: max(1, int(limit))]

    def get_representative_wrong_items_for_error(
        self,
        error_category: str,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        rows = self._query_active_detail_rows(student_id=student_id, session_ids=session_ids)
        result: list[dict[str, Any]] = []
        for item in self._enrich_detail_rows(rows):
            if not item.get("is_deducted"):
                continue
            category = _normalize_error_category(item.get("error_category"), str(item.get("deduction_reason") or ""))
            if category != error_category:
                continue
            item["error_category"] = category
            if not item.get("error_summary"):
                item["error_summary"] = _short_reason(str(item.get("deduction_reason") or ""))
            result.append(item)
        random.shuffle(result)
        return sorted(
            result,
            key=lambda item: (
                float(item.get("score_rate") or 0),
                str(item.get("graded_at") or ""),
            ),
        )[: max(1, int(limit))]

    def _query_active_detail_rows(
        self,
        *,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                sr.session_id,
                gs.session_name,
                sr.id AS result_id,
                sr.student_id,
                s.student_code,
                s.name AS student_name,
                ep.front_image,
                ep.back_image,
                sd.question_id,
                sd.knowledge_id,
                sd.knowledge_ids,
                sd.score_awarded,
                sd.deduction_reason,
                sd.error_category,
                sd.error_summary,
                sr.graded_at
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN grading_sessions gs ON gs.id = sr.session_id
            JOIN students s ON s.id = sr.student_id
            JOIN exam_papers ep ON ep.id = sr.paper_id
            WHERE COALESCE(gs.is_deleted, 0) = 0
        """
        params: list[Any] = []
        if student_id is not None:
            query += " AND sr.student_id = ?"
            params.append(int(student_id))
        if session_ids:
            placeholders = ",".join(["?"] * len(session_ids))
            query += f" AND sr.session_id IN ({placeholders})"
            params.extend(int(value) for value in session_ids)
        query += " ORDER BY sr.graded_at DESC, sd.id ASC"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def _enrich_detail_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        knowledge_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}
        for row in rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            qid = str(row.get("question_id") or "")
            row_knowledge_ids = rubric_cache[session_id].get("knowledge", {}).get(qid) or _knowledge_ids_from_row(row)
            if knowledge_id is not None and not _knowledge_id_matches(knowledge_id, row_knowledge_ids):
                continue
            full_score = rubric_cache[session_id]["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            reason = str(row.get("deduction_reason") or "")
            max_score = full_score if full_score is not None else awarded
            row = dict(row)
            row["max_score"] = max_score
            row["score_rate"] = round(awarded / max_score * 100, 2) if max_score else 0.0
            row["is_deducted"] = _is_deducted(awarded, full_score, reason)
            row["deduction_amount"] = max(0.0, round((max_score or 0.0) - awarded, 2))
            if knowledge_id:
                row["knowledge_label"] = rubric_cache[session_id]["label"].get(
                    knowledge_id,
                    _fallback_knowledge_label(knowledge_id, qid),
                )
            result.append(row)
        return result

    def _build_weak_point_rows(self, detail_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[Any, str], dict[str, Any]] = {}
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}

        for row in detail_rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            maps = rubric_cache[session_id]
            qid = str(row.get("question_id") or "")
            knowledge_ids = maps.get("knowledge", {}).get(qid) or _knowledge_ids_from_row(row)
            full_score = maps["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            full_score_value = _safe_float(full_score, 0.0)
            if full_score_value > 0:
                score_for_rate = min(max(awarded, 0.0), full_score_value)
            else:
                full_score_value = max(awarded, 0.0)
                score_for_rate = max(awarded, 0.0)
            reason = str(row.get("deduction_reason") or "").strip()
            deducted = _is_deducted(awarded, full_score, reason)

            for knowledge_id in knowledge_ids:
                # 当 knowledge_id 是 UNKNOWN 时，尝试获取该题目特定的中文标签
                if knowledge_id == "UNKNOWN":
                    label = maps["label"].get(f"{qid}_UNKNOWN")
                    # 如果找到了中文标签，则直接用该中文标签作为 knowledge_id，实现按实际知识点名称分组
                    if label and _has_chinese(label):
                        effective_kid = label
                    else:
                        effective_kid = qid
                else:
                    effective_kid = knowledge_id
                    label = maps["label"].get(effective_kid)

                if not label:
                    # 从 registry 查找标准中文名称（处理 C2_01、K1、JSSX_xxx 等各种代码格式）
                    label = _knowledge_label_from_registry(effective_kid)
                if not label:
                    label = _fallback_knowledge_label(effective_kid, qid)
                key = (row.get("student_id"), _knowledge_group_key(effective_kid, label))
                item = grouped.setdefault(
                    key,
                    {
                        "student_id": row.get("student_id"),
                        "student_code": row.get("student_code"),
                        "student_name": row.get("student_name"),
                        "class_name": row.get("class_name"),
                        "knowledge_id": effective_kid,
                        "knowledge_ids": [],
                        "knowledge_label": label,
                        "score_sum": 0.0,
                        "full_score_sum": 0.0,
                        "item_count": 0,
                        "deduction_count": 0,
                        "exam_ids": set(),
                        "reasons": set(),
                    },
                )
                if effective_kid not in item["knowledge_ids"]:
                    item["knowledge_ids"].append(effective_kid)
                item["score_sum"] += score_for_rate
                item["full_score_sum"] += full_score_value
                item["item_count"] += 1
                item["exam_ids"].add(session_id)
                if deducted:
                    item["deduction_count"] += 1
                    if _is_real_deduction_reason(reason):
                        item["reasons"].add(reason)

        result: list[dict[str, Any]] = []
        for item in grouped.values():
            item_count = max(1, int(item["item_count"]))
            full_score_sum = float(item.get("full_score_sum") or 0.0)
            score_sum = float(item.get("score_sum") or 0.0)
            weighted_score_rate = round(score_sum / full_score_sum * 100, 2) if full_score_sum > 0 else 100.0
            result.append(
                {
                    "student_id": item["student_id"],
                    "student_code": item["student_code"],
                    "student_name": item["student_name"],
                    "class_name": item["class_name"],
                    "knowledge_id": item["knowledge_id"],
                    "knowledge_ids": item["knowledge_ids"],
                    "knowledge_label": item["knowledge_label"],
                    "avg_score": round(score_sum / item_count, 2),
                    "score_sum": round(score_sum, 2),
                    "full_score_sum": round(full_score_sum, 2),
                    "weighted_score_rate": weighted_score_rate,
                    "deduction_count": int(item["deduction_count"]),
                    "item_count": item_count,
                    "exam_count": len(item["exam_ids"]),
                    "sample_reasons": "；".join(sorted(item["reasons"])),
                }
            )

        return sorted(
            result,
            key=lambda item: (
                _safe_float(item.get("weighted_score_rate"), 100.0),
                -int(item.get("deduction_count") or 0),
                str(item.get("knowledge_id") or ""),
            ),
        )

    def _build_error_point_rows(self, detail_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[Any, str], dict[str, Any]] = {}
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}

        for row in detail_rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            qid = str(row.get("question_id") or "")
            full_score = rubric_cache[session_id]["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            reason = str(row.get("deduction_reason") or "").strip()
            if not _is_deducted(awarded, full_score, reason):
                continue

            full_score_value = _safe_float(full_score, 0.0)
            if full_score_value <= 0:
                full_score_value = max(awarded, 0.0)
            category = _normalize_error_category(row.get("error_category"), reason)
            summary = str(row.get("error_summary") or "").strip() or _short_reason(reason)
            key = (row.get("student_id"), category)
            item = grouped.setdefault(
                key,
                {
                    "student_id": row.get("student_id"),
                    "student_code": row.get("student_code"),
                    "student_name": row.get("student_name"),
                    "class_name": row.get("class_name"),
                    "error_category": category,
                    "score_sum": 0.0,
                    "full_score_sum": 0.0,
                    "item_count": 0,
                    "deduction_count": 0,
                    "exam_ids": set(),
                    "summaries": set(),
                },
            )
            item["score_sum"] += min(max(awarded, 0.0), full_score_value)
            item["full_score_sum"] += full_score_value
            item["item_count"] += 1
            item["deduction_count"] += 1
            item["exam_ids"].add(session_id)
            if summary:
                item["summaries"].add(summary)

        result: list[dict[str, Any]] = []
        for item in grouped.values():
            full_score_sum = float(item.get("full_score_sum") or 0.0)
            score_sum = float(item.get("score_sum") or 0.0)
            score_rate = round(score_sum / full_score_sum * 100, 2) if full_score_sum > 0 else 0.0
            result.append(
                {
                    "student_id": item["student_id"],
                    "student_code": item["student_code"],
                    "student_name": item["student_name"],
                    "class_name": item["class_name"],
                    "error_category": item["error_category"],
                    "score_sum": round(score_sum, 2),
                    "full_score_sum": round(full_score_sum, 2),
                    "weighted_score_rate": score_rate,
                    "deduction_count": int(item["deduction_count"]),
                    "item_count": int(item["item_count"]),
                    "exam_count": len(item["exam_ids"]),
                    "sample_reasons": "；".join(sorted(item["summaries"])),
                }
            )
        return sorted(
            result,
            key=lambda item: (
                -int(item.get("deduction_count") or 0),
                _safe_float(item.get("weighted_score_rate"), 100.0),
                str(item.get("error_category") or ""),
            ),
        )

    def _load_rubric_maps_for_session(self, session_id: int) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_rubric_map_cache"):
            self._rubric_map_cache = {}
        if session_id in self._rubric_map_cache:
            return self._rubric_map_cache[session_id]

        session = self.get_grading_session(session_id)
        score_map: dict[str, float] = {}
        label_map: dict[str, str] = {}
        knowledge_map: dict[str, list[str]] = {}
        result = {"score": score_map, "label": label_map, "knowledge": knowledge_map}

        if not session:
            self._rubric_map_cache[session_id] = result
            return result

        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            self._rubric_map_cache[session_id] = result
            return result
        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            self._rubric_map_cache[session_id] = result
            return result

        questions = rubric.get("questions") if isinstance(rubric, dict) else []
        if not isinstance(questions, list):
            return {"score": score_map, "label": label_map, "knowledge": knowledge_map}
        for question in questions:
            if not isinstance(question, dict):
                continue
            qid = str(question.get("question_id") or "").strip()
            knowledge_ids = _knowledge_ids_from_question(question)
            if qid:
                score_map[qid] = _safe_float(question.get("max_score"), 0.0)
                knowledge_map[qid] = knowledge_ids
            for kid in knowledge_ids:
                lbl = _knowledge_label_from_question(kid, question)
                label_map.setdefault(kid, lbl)
                if kid == "UNKNOWN" and qid:
                    label_map[f"{qid}_UNKNOWN"] = lbl
            parts = question.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    pid = str(part.get("part_id") or "").strip()
                    if pid:
                        score_map[pid] = _safe_float(part.get("part_score"), 0.0)
                        knowledge_map[pid] = _normalize_knowledge_ids(
                            part.get("knowledge_points") or part.get("knowledge_ids"),
                            part.get("knowledge_id"),
                        ) or knowledge_ids
                        for kid in knowledge_map[pid]:
                            lbl = _knowledge_label_from_question(kid, question)
                            label_map.setdefault(kid, lbl)
                            if kid == "UNKNOWN" and pid:
                                label_map[f"{pid}_UNKNOWN"] = lbl
        return {"score": score_map, "label": label_map, "knowledge": knowledge_map}

    def _resolve_stored_file_path(self, path_value: object) -> Path:
        data_root = self.db_path.parent.parent if self.db_path.parent.name == "databases" else None
        return resolve_stored_file_path(path_value, data_root=data_root)



def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _detail_knowledge_ids(detail: Any) -> list[str]:
    values = getattr(detail, "knowledge_ids", None)
    fallback = getattr(detail, "knowledge_id", None)
    result = _normalize_knowledge_ids(values, fallback)
    return result or ["UNKNOWN"]


def _knowledge_ids_from_row(row: dict[str, Any]) -> list[str]:
    return _normalize_knowledge_ids(row.get("knowledge_ids"), row.get("knowledge_id")) or ["UNKNOWN"]


def _knowledge_ids_from_question(question: dict[str, Any]) -> list[str]:
    return _normalize_knowledge_ids(
        question.get("knowledge_points") or question.get("knowledge_ids"),
        question.get("knowledge_id"),
    ) or ["UNKNOWN"]


def _knowledge_id_filter_values(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").replace("|", ",").split(",") if part.strip()}


def _knowledge_id_matches(filter_value: str, candidate_ids: list[str]) -> bool:
    filters = _knowledge_id_filter_values(filter_value)
    return bool(filters.intersection({str(item).strip() for item in candidate_ids}))


def _knowledge_group_key(knowledge_id: str, knowledge_label: str) -> str:
    label = str(knowledge_label or "").strip()
    kid = str(knowledge_id or "").strip()
    if kid and label.startswith(kid):
        label = label[len(kid):].strip()
        for separator in ("·", "：", ":", "-", "|", " "):
            label = label.removeprefix(separator).strip()
    return label or kid or "UNKNOWN"


def _normalize_knowledge_ids(raw: Any, fallback: Any = None) -> list[str]:
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif isinstance(raw, str) and raw.strip():
        parsed = _safe_json_loads(raw)
        if isinstance(parsed, list):
            values.extend(parsed)
        else:
            values.extend(_split_knowledge_text(raw))
    elif raw:
        values.append(raw)

    if fallback:
        if isinstance(fallback, list):
            values.extend(fallback)
        else:
            values.extend(_split_knowledge_text(str(fallback)))

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            kid = str(value.get("knowledge_id") or value.get("id") or "").strip()
        else:
            kid = str(value or "").strip()
        if not kid or kid in seen:
            continue
        seen.add(kid)
        result.append(kid)
    return result


def _split_knowledge_text(text: str) -> list[str]:
    normalized = text.replace("，", ",").replace("；", ",").replace(";", ",").replace("|", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_real_deduction_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    if not text:
        return False
    positive_markers = {
        "正确",
        "全对",
        "无扣分",
        "未扣分",
        "答案正确",
        "过程正确",
        "ok",
        "right",
        "correct",
        "none",
        "null",
        "无",
    }
    return text not in positive_markers


def _is_deducted(score_awarded: float, full_score: float | None, reason: str) -> bool:
    if full_score is not None and full_score > 0:
        return score_awarded < full_score - 0.01
    return _is_real_deduction_reason(reason)


def _normalize_error_category(raw: Any, reason: str = "") -> str:
    text = str(raw or "").strip()
    allowed = {
        "概念理解错误",
        "计算错误",
        "审题错误",
        "条件遗漏",
        "逻辑断裂",
        "表达不规范",
        "未作答",
        "多选失分",
        "作废答案",
        "提示注入",
        "答案不等价",
        "其他",
    }
    if text in allowed:
        return text
    reason_text = str(reason or "")
    if any(token in reason_text for token in ["多选", "多个选项", "AB", "AC", "AD", "BC", "BD", "CD"]):
        return "多选失分"
    if any(token in reason_text for token in ["未作答", "空白", "没有写", "未写"]):
        return "未作答"
    if any(token in reason_text for token in ["划掉", "作废", "删除线", "打叉"]):
        return "作废答案"
    if any(token in reason_text for token in ["请打满分", "忽略", "prompt", "AI"]):
        return "提示注入"
    if any(token in reason_text for token in ["计算", "算错", "化简", "数值"]):
        return "计算错误"
    if any(token in reason_text for token in ["审题", "看错", "条件理解"]):
        return "审题错误"
    if any(token in reason_text for token in ["条件", "前提", "已知"]):
        return "条件遗漏"
    if any(token in reason_text for token in ["逻辑", "证明", "推出", "全等", "断裂"]):
        return "逻辑断裂"
    if any(token in reason_text for token in ["等价", "不等价", "答案不符"]):
        return "答案不等价"
    if any(token in reason_text for token in ["表达", "书写", "格式", "符号"]):
        return "表达不规范"
    return "其他"


def _short_reason(reason: str, max_len: int = 36) -> str:
    text = " ".join(str(reason or "").replace("\n", " ").split())
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _knowledge_label_from_question(knowledge_id: str, question: dict[str, Any]) -> str:
    kid = str(knowledge_id or "").strip()
    point_label = _knowledge_point_labels_from_question(question).get(kid)
    if point_label:
        return _format_knowledge_label(kid, point_label)

    candidates = [
        question.get("knowledge_name"),
        question.get("knowledge_text"),
        question.get("knowledge_label"),
    ]
    obligations = question.get("proof_obligations")
    if isinstance(obligations, list):
        for obligation in obligations:
            if isinstance(obligation, dict):
                candidates.append(obligation.get("description"))
    parts = question.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            steps = part.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict):
                        candidates.append(step.get("core_goal"))

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and text.lower() not in {"direct-answer", "direct_answer", "正确", "unknown"}:
            # 如果候选文本不含中文（如英文 core_goal 字段），则跳过，避免显示英文
            if not _has_chinese(text):
                continue
            return _format_knowledge_label(kid, text[:28])
    return _fallback_knowledge_label(knowledge_id, str(question.get("question_id") or ""))


def _knowledge_point_labels_from_question(question: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    raw_points = question.get("knowledge_points")
    if isinstance(raw_points, list):
        for point in raw_points:
            if isinstance(point, dict):
                kid = str(point.get("knowledge_id") or point.get("id") or "").strip()
                label = str(
                    point.get("knowledge_name")
                    or point.get("name")
                    or point.get("knowledge_label")
                    or point.get("label")
                    or point.get("knowledge_text")
                    or ""
                ).strip()
                if kid and label:
                    labels.setdefault(kid, label)
                elif label:
                    labels.setdefault(label, label)
            else:
                for label in _split_knowledge_text(str(point or "")):
                    labels.setdefault(label, label)
    elif isinstance(raw_points, str):
        for label in _split_knowledge_text(raw_points):
            labels.setdefault(label, label)

    primary_kid = str(question.get("knowledge_id") or "").strip()
    primary_label = str(
        question.get("knowledge_name")
        or question.get("knowledge_label")
        or question.get("knowledge_text")
        or ""
    ).strip()
    if primary_kid and primary_label:
        labels.setdefault(primary_kid, primary_label)
    return labels


def _format_knowledge_label(knowledge_id: str, label: str) -> str:
    kid = str(knowledge_id or "").strip()
    text = str(label or "").strip()
    if not kid or kid == "UNKNOWN":
        return text
    if not text or text == kid:
        return kid
    return f"{kid} · {text}"


def _fallback_knowledge_label(knowledge_id: str, question_id: str) -> str:
    return knowledge_id


def _has_chinese(text: str) -> bool:
    """判断字符串是否含有中文字符（汉字）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _knowledge_label_from_registry(knowledge_id: str) -> str:
    """从 taxonomy registry 把知识点代码映射为标准中文名称。
    
    - 如果 knowledge_id 本身是中文（如 "等腰三角形性质"），直接返回
    - 如果是代码格式（C2_01、K1 等），在 registry 中查 alias
    - 查不到则返回空字符串（让调用方继续回退）
    """
    kid = str(knowledge_id or "").strip()
    if not kid or kid == "UNKNOWN":
        return ""
    # knowledge_id 本身是中文：直接作为标签
    if _has_chinese(kid):
        return kid
    # 通过 registry 查找
    if _registry_canonicalize is not None:
        try:
            canonical = _registry_canonicalize(kid)
            if canonical is not None:
                return canonical.canonical_name
        except Exception:
            pass
    return ""


def _normalize_name(value: str) -> str:
    return value.strip().replace(" ", "").replace("\u3000", "").replace("\u00b7", "").lower()
