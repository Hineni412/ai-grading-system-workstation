"""Student roster persistence and transaction boundaries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider


@dataclass
class StudentRecord:
    student_code: str
    name: str
    class_name: str | None = None


class StudentRosterRevisionConflict(ValueError):
    pass


class StudentCodeConflictError(ValueError):
    pass


class StudentBackupFailedError(RuntimeError):
    pass


class StudentGradingActiveError(RuntimeError):
    pass


def student_roster_revision(rows: list[dict[str, Any]]) -> str:
    payload = [
        {
            "id": int(row["id"]),
            "student_code": str(row["student_code"]),
            "name": str(row["name"]),
            "class_name": row.get("class_name"),
            "created_at": row.get("created_at"),
        }
        for row in sorted(rows, key=lambda item: int(item["id"]))
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_name(value: str) -> str:
    return (
        value.strip()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("\u00b7", "")
        .lower()
    )


class StudentRepository:
    """Session-bound student SQL without connection or transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def list_students(self) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT id, student_code, name, class_name, created_at
            FROM students
            ORDER BY class_name ASC, name ASC, id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_students(self, students: list[StudentRecord]) -> dict[str, int]:
        codes = [student.student_code for student in students]
        placeholders = ",".join(["?"] * len(codes))
        existing_rows = self.session.connection.execute(
            f"""
            SELECT id, student_code, name, class_name
            FROM students
            WHERE student_code IN ({placeholders})
            """,
            codes,
        ).fetchall()
        existing_map = {row["student_code"]: row for row in existing_rows}
        to_insert: list[tuple[str, str, str | None]] = []
        to_update: list[tuple[str, str | None, int]] = []
        for student in students:
            existing = existing_map.get(student.student_code)
            if existing is None:
                to_insert.append(
                    (student.student_code, student.name, student.class_name)
                )
            elif (
                existing["name"] != student.name
                or (existing["class_name"] or "") != (student.class_name or "")
            ):
                to_update.append(
                    (student.name, student.class_name, int(existing["id"]))
                )
        if to_insert:
            self.session.connection.executemany(
                """
                INSERT INTO students (student_code, name, class_name)
                VALUES (?, ?, ?)
                """,
                to_insert,
            )
        if to_update:
            self.session.connection.executemany(
                """
                UPDATE students
                SET name = ?, class_name = ?
                WHERE id = ?
                """,
                to_update,
            )
        return {
            "inserted": len(to_insert),
            "updated": len(to_update),
            "total": len(to_insert) + len(to_update),
        }

    def update_student(
        self,
        student_id: int,
        student_code: str,
        name: str,
        class_name: str | None,
    ) -> None:
        clean_code = str(student_code or "").strip()
        clean_name = str(name or "").strip()
        clean_class_name = str(class_name or "").strip() or None
        if not clean_code:
            raise ValueError("学生学号不能为空")
        if not clean_name:
            raise ValueError("学生姓名不能为空")
        cursor = self.session.connection.execute(
            """
            UPDATE students
            SET student_code = ?, name = ?, class_name = ?
            WHERE id = ?
            """,
            (clean_code, clean_name, clean_class_name, int(student_id)),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"未找到学生记录: {student_id}")

    def student_workspace_snapshot(
        self,
        *,
        search: str,
        class_name: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if search:
            escaped = (
                search.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            clauses.append(
                """
                (
                    student_code LIKE ? ESCAPE '\\'
                    OR name LIKE ? ESCAPE '\\'
                    OR COALESCE(class_name, '') LIKE ? ESCAPE '\\'
                )
                """
            )
            parameters.extend([pattern, pattern, pattern])
        if class_name:
            clauses.append("COALESCE(class_name, '') = ?")
            parameters.append(class_name)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = (page - 1) * page_size
        total = int(
            self.session.connection.execute(
                f"SELECT COUNT(*) FROM students {where_sql}",
                parameters,
            ).fetchone()[0]
        )
        page_rows = self.session.connection.execute(
            f"""
            SELECT id, student_code, name, class_name, created_at
            FROM students
            {where_sql}
            ORDER BY class_name ASC, name ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, offset],
        ).fetchall()
        class_rows = self.session.connection.execute(
            """
            SELECT DISTINCT class_name
            FROM students
            WHERE COALESCE(class_name, '') <> ''
            ORDER BY class_name ASC
            """
        ).fetchall()
        return {
            "items": [dict(row) for row in page_rows],
            "total": total,
            "class_names": [str(row["class_name"]) for row in class_rows],
        }

    def students_for_name_lookup(self) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            "SELECT id, student_code, name, class_name FROM students"
        ).fetchall()
        return [dict(row) for row in rows]

    def deletion_impact(self, student_id: int) -> dict[str, int]:
        return {
            "deleted_students": int(
                self.session.connection.execute(
                    "SELECT COUNT(*) FROM students WHERE id = ?",
                    (int(student_id),),
                ).fetchone()[0]
            ),
            "deleted_results": int(
                self.session.connection.execute(
                    "SELECT COUNT(*) FROM session_results WHERE student_id = ?",
                    (int(student_id),),
                ).fetchone()[0]
            ),
            "deleted_details": int(
                self.session.connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM session_details detail
                    JOIN session_results result ON result.id = detail.result_id
                    WHERE result.student_id = ?
                    """,
                    (int(student_id),),
                ).fetchone()[0]
            ),
            "deleted_annotations": int(
                self.session.connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM annotated_results annotation
                    JOIN session_results result ON result.id = annotation.result_id
                    WHERE result.student_id = ?
                    """,
                    (int(student_id),),
                ).fetchone()[0]
            ),
            "deleted_attendance": int(
                self.session.connection.execute(
                    "SELECT COUNT(*) FROM session_attendance WHERE student_id = ?",
                    (int(student_id),),
                ).fetchone()[0]
            ),
            "unlinked_papers": int(
                self.session.connection.execute(
                    "SELECT COUNT(*) FROM exam_papers WHERE student_id = ?",
                    (int(student_id),),
                ).fetchone()[0]
            ),
        }

    def has_active_grading(self) -> bool:
        active = self.session.connection.execute(
            """
            SELECT 1
            FROM grading_sessions
            WHERE status = 'running'
            LIMIT 1
            """
        ).fetchone()
        if active is not None:
            return True
        if not self._table_exists("grading_runs"):
            return False
        return (
            self.session.connection.execute(
                """
                SELECT 1
                FROM grading_runs
                WHERE state IN ('running', 'pause_requested', 'paused')
                LIMIT 1
                """
            ).fetchone()
            is not None
        )

    def delete_student_history(self, student_id: int) -> dict[str, int]:
        if self._table_exists("grading_run_items"):
            self.session.connection.execute(
                "DELETE FROM grading_run_items WHERE student_id = ?",
                (int(student_id),),
            )
        result_rows = self.session.connection.execute(
            "SELECT id FROM session_results WHERE student_id = ?",
            (int(student_id),),
        ).fetchall()
        result_ids = [int(row["id"]) for row in result_rows]
        deleted_details = 0
        deleted_annotations = 0
        if result_ids:
            placeholders = ",".join(["?"] * len(result_ids))
            deleted_details = self.session.connection.execute(
                f"DELETE FROM session_details WHERE result_id IN ({placeholders})",
                result_ids,
            ).rowcount
            deleted_annotations = self.session.connection.execute(
                f"DELETE FROM annotated_results WHERE result_id IN ({placeholders})",
                result_ids,
            ).rowcount
        deleted_attendance = self.session.connection.execute(
            "DELETE FROM session_attendance WHERE student_id = ?",
            (int(student_id),),
        ).rowcount
        deleted_results = self.session.connection.execute(
            "DELETE FROM session_results WHERE student_id = ?",
            (int(student_id),),
        ).rowcount
        unlinked_papers = self.session.connection.execute(
            """
            UPDATE exam_papers
            SET student_id = NULL,
                match_status = 'student_deleted',
                processing_status = 'pending',
                error_message = NULL
            WHERE student_id = ?
            """,
            (int(student_id),),
        ).rowcount
        deleted_students = self.session.connection.execute(
            "DELETE FROM students WHERE id = ?",
            (int(student_id),),
        ).rowcount
        return {
            "deleted_students": int(deleted_students),
            "deleted_results": int(deleted_results),
            "deleted_details": int(deleted_details),
            "deleted_annotations": int(deleted_annotations),
            "deleted_attendance": int(deleted_attendance),
            "unlinked_papers": int(unlinked_papers),
        }

    def _table_exists(self, table_name: str) -> bool:
        return (
            self.session.connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (table_name,),
            ).fetchone()
            is not None
        )


class StudentRepositoryGateway:
    """Open one repository session per roster operation."""

    def __init__(
        self,
        sessions: RepositorySessionProvider,
        *,
        create_backup: Callable[[str], Any] | None = None,
    ) -> None:
        self._sessions = sessions
        self._create_backup = create_backup
        self._cached_students_for_find: list[dict[str, Any]] | None = None

    def _invalidate_name_cache(self) -> None:
        self._cached_students_for_find = None

    def upsert_students(self, students: list[StudentRecord]) -> dict[str, int]:
        if not students:
            return {"inserted": 0, "updated": 0, "total": 0}
        with self._sessions.session() as session:
            with session.transaction():
                result = StudentRepository(session).upsert_students(students)
        self._invalidate_name_cache()
        return result

    def list_students(self) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return StudentRepository(session).list_students()

    def student_roster_snapshot(self) -> tuple[list[dict[str, Any]], str]:
        rows = self.list_students()
        return rows, student_roster_revision(rows)

    def student_workspace_snapshot(
        self,
        *,
        search: str,
        class_name: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        with self._sessions.session(read_only=True) as session:
            with session.transaction():
                repository = StudentRepository(session)
                all_rows = repository.list_students()
                snapshot = repository.student_workspace_snapshot(
                    search=search,
                    class_name=class_name,
                    page=page,
                    page_size=page_size,
                )
        return {
            **snapshot,
            "roster_revision": student_roster_revision(all_rows),
        }

    def update_student(
        self,
        student_id: int,
        student_code: str,
        name: str,
        class_name: str | None,
    ) -> None:
        try:
            with self._sessions.session() as session:
                with session.transaction():
                    StudentRepository(session).update_student(
                        student_id,
                        student_code,
                        name,
                        class_name,
                    )
        except sqlite3.IntegrityError as exc:
            clean_code = str(student_code or "").strip()
            raise ValueError(f"学号已存在，无法保存: {clean_code}") from exc
        self._invalidate_name_cache()

    def find_student_by_name(self, name: str) -> dict[str, Any] | None:
        normalized_target = _normalize_name(name)
        if not normalized_target:
            return None
        if self._cached_students_for_find is None:
            with self._sessions.session(read_only=True) as session:
                self._cached_students_for_find = StudentRepository(
                    session
                ).students_for_name_lookup()
        for row in self._cached_students_for_find:
            if _normalize_name(str(row["name"])) == normalized_target:
                return row
        return None

    def student_deletion_impact(self, student_id: int) -> dict[str, Any]:
        with self._sessions.session(read_only=True) as session:
            with session.transaction():
                repository = StudentRepository(session)
                rows = repository.list_students()
                student = next(
                    (row for row in rows if int(row["id"]) == int(student_id)),
                    None,
                )
                if student is None:
                    raise ValueError(f"未找到学生记录: {student_id}")
                counts = repository.deletion_impact(student_id)
        return {
            "student": student,
            "counts": counts,
            "roster_revision": student_roster_revision(rows),
        }

    def delete_student_hard(
        self,
        student_id: int,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                repository = StudentRepository(session)
                current_rows = repository.list_students()
                student = next(
                    (
                        row
                        for row in current_rows
                        if int(row["id"]) == int(student_id)
                    ),
                    None,
                )
                if student is None:
                    raise ValueError(f"未找到学生记录: {student_id}")
                if (
                    expected_revision is not None
                    and student_roster_revision(current_rows)
                    != str(expected_revision)
                ):
                    raise StudentRosterRevisionConflict(
                        "Student roster changed before delete"
                    )
                if repository.has_active_grading():
                    raise StudentGradingActiveError(
                        "Student has an active grading run"
                    )
                try:
                    backup_path = (
                        None
                        if self._create_backup is None
                        else self._create_backup("delete_student")
                    )
                except Exception as exc:
                    raise StudentBackupFailedError(
                        "Student backup failed before delete"
                    ) from exc
                if backup_path is None:
                    raise StudentBackupFailedError(
                        "Student backup was not created before delete"
                    )
                result = repository.delete_student_history(student_id)
                next_revision = student_roster_revision(
                    repository.list_students()
                )
        self._invalidate_name_cache()
        return {
            **result,
            "backup_created": True,
            "roster_revision": next_revision,
        }

    def upsert_students_if_revision(
        self,
        students: list[StudentRecord],
        *,
        expected_revision: str,
    ) -> dict[str, int | str]:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                repository = StudentRepository(session)
                current_rows = repository.list_students()
                if student_roster_revision(current_rows) != str(expected_revision):
                    raise StudentRosterRevisionConflict(
                        "Student roster changed after preview"
                    )
                result = repository.upsert_students(students)
                next_rows = repository.list_students()
                next_revision = student_roster_revision(next_rows)
        self._invalidate_name_cache()
        return {**result, "roster_revision": next_revision}

    def update_student_if_revision(
        self,
        student_id: int,
        student_code: str,
        name: str,
        class_name: str | None,
        *,
        expected_revision: str,
    ) -> dict[str, Any]:
        try:
            with self._sessions.session() as session:
                with session.transaction(immediate=True):
                    repository = StudentRepository(session)
                    current_rows = repository.list_students()
                    if student_roster_revision(current_rows) != str(expected_revision):
                        raise StudentRosterRevisionConflict(
                            "Student roster changed before update"
                        )
                    repository.update_student(
                        student_id,
                        student_code,
                        name,
                        class_name,
                    )
                    next_rows = repository.list_students()
                    student = next(
                        (
                            row
                            for row in next_rows
                            if int(row["id"]) == int(student_id)
                        ),
                        None,
                    )
                    if student is None:
                        raise ValueError(f"未找到学生记录: {student_id}")
                    next_revision = student_roster_revision(next_rows)
        except sqlite3.IntegrityError as exc:
            raise StudentCodeConflictError("Student code already exists") from exc
        self._invalidate_name_cache()
        return {
            "student": student,
            "roster_revision": next_revision,
        }
