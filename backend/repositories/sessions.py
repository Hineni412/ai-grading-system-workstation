"""Grading-session and attendance persistence boundaries."""

from __future__ import annotations

import re
from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider


def _validated_source_binding(
    source_paper_path: object,
    source_paper_sha256: object,
) -> tuple[str, str]:
    source_path = str(source_paper_path or "").strip()
    source_sha256 = str(source_paper_sha256 or "").strip().lower()
    if not source_path and not source_sha256:
        return "", ""
    if not source_path or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("source paper path and full SHA-256 are required")
    return source_path, source_sha256


class SessionRepository:
    """Session-bound SQL without connection or transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def create_grading_session(
        self,
        session_name: str,
        rubric_path: str,
        answer_key_path: str,
        *,
        source_paper_path: str = "",
        source_paper_sha256: str = "",
    ) -> int:
        source_path, source_sha256 = _validated_source_binding(
            source_paper_path,
            source_paper_sha256,
        )
        cursor = self.session.connection.execute(
            """
            INSERT INTO grading_sessions (
                session_name, rubric_path, answer_key_path, status, is_deleted,
                source_paper_path, source_paper_sha256, updated_at
            )
            VALUES (?, ?, ?, 'created', 0, ?, ?, datetime('now','localtime'))
            """,
            (
                session_name,
                rubric_path,
                answer_key_path,
                source_path or None,
                source_sha256 or None,
            ),
        )
        return int(cursor.lastrowid)

    def rename_grading_session(self, session_id: int, new_name: str) -> None:
        self.session.connection.execute(
            """
            UPDATE grading_sessions
            SET session_name = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (new_name, session_id),
        )

    def soft_delete_grading_session(self, session_id: int) -> None:
        self.session.connection.execute(
            """
            UPDATE grading_sessions
            SET is_deleted = 1,
                deleted_at = datetime('now','localtime'),
                updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (session_id,),
        )

    def restore_grading_session(self, session_id: int) -> None:
        self.session.connection.execute(
            """
            UPDATE grading_sessions
            SET is_deleted = 0,
                deleted_at = NULL,
                updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (session_id,),
        )

    def list_grading_sessions(
        self,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id, session_name, rubric_path, answer_key_path, template_config_path,
                   status, is_deleted, deleted_at, source_paper_path, source_paper_sha256,
                   question_bank_sync_state, question_bank_sync_details_json,
                   question_bank_sync_error, question_bank_sync_updated_at,
                   created_at, updated_at
            FROM grading_sessions
        """
        if not include_deleted:
            query += " WHERE is_deleted = 0"
        query += " ORDER BY id DESC"
        rows = self.session.connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_grading_session(self, session_id: int) -> dict[str, Any] | None:
        row = self.session.connection.execute(
            """
            SELECT id, session_name, rubric_path, answer_key_path, template_config_path,
                   status, is_deleted, deleted_at, source_paper_path, source_paper_sha256,
                   question_bank_sync_state, question_bank_sync_details_json,
                   question_bank_sync_error, question_bank_sync_updated_at,
                   created_at, updated_at
            FROM grading_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def replace_session_attendance(
        self,
        session_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        self.session.connection.execute(
            "DELETE FROM session_attendance WHERE session_id = ?",
            (session_id,),
        )
        for row in rows:
            self.session.connection.execute(
                """
                INSERT INTO session_attendance (
                    session_id, student_id, attendance_status, source_reason,
                    matched_paper_id
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

    def get_session_attendance(self, session_id: int) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
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


class SessionRepositoryGateway:
    """Open one repository session per grading-session operation."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

    def create_grading_session(
        self,
        session_name: str,
        rubric_path: str,
        answer_key_path: str,
        *,
        source_paper_path: str = "",
        source_paper_sha256: str = "",
    ) -> int:
        with self._sessions.session() as session:
            with session.transaction():
                return SessionRepository(session).create_grading_session(
                    session_name,
                    rubric_path,
                    answer_key_path,
                    source_paper_path=source_paper_path,
                    source_paper_sha256=source_paper_sha256,
                )

    def rename_grading_session(self, session_id: int, new_name: str) -> None:
        self._write("rename_grading_session", session_id, new_name)

    def soft_delete_grading_session(self, session_id: int) -> None:
        self._write("soft_delete_grading_session", session_id)

    def restore_grading_session(self, session_id: int) -> None:
        self._write("restore_grading_session", session_id)

    def _write(self, method_name: str, *args: Any) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                getattr(SessionRepository(session), method_name)(*args)

    def list_grading_sessions(
        self,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return SessionRepository(session).list_grading_sessions(include_deleted)

    def get_grading_session(self, session_id: int) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return SessionRepository(session).get_grading_session(session_id)

    def replace_session_attendance(
        self,
        session_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                SessionRepository(session).replace_session_attendance(session_id, rows)

    def get_session_attendance(self, session_id: int) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return SessionRepository(session).get_session_attendance(session_id)
