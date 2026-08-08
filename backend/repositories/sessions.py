"""Grading-session and attendance persistence boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider


class SessionDeletionRevisionConflict(RuntimeError):
    """The session changed after the user reviewed the delete action."""


class SessionDeletionConfirmationMismatch(ValueError):
    """The typed session name does not exactly match the stored name."""


class SessionDeletionActiveWork(RuntimeError):
    """Reversible deletion is blocked while session work can still mutate data."""

    def __init__(self, *, active_jobs: int, active_grading_runs: int) -> None:
        super().__init__("Session work is still active")
        self.active_jobs = int(active_jobs)
        self.active_grading_runs = int(active_grading_runs)


class SessionPermanentDeletionRequiresArchive(RuntimeError):
    """Permanent deletion is allowed only after the session is archived."""


def session_deletion_revision(row: dict[str, Any]) -> str:
    """Return the stable revision used by the delete preview/commit handshake."""

    payload = {
        str(key): (
            value.hex()
            if isinstance(value, bytes)
            else value
            if value is None or isinstance(value, (str, int, float, bool))
            else str(value)
        )
        for key, value in sorted(row.items(), key=lambda item: str(item[0]))
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        curriculum_volume_id: str | None = None,
    ) -> int:
        source_path, source_sha256 = _validated_source_binding(
            source_paper_path,
            source_paper_sha256,
        )
        cursor = self.session.connection.execute(
            """
            INSERT INTO grading_sessions (
                session_name, rubric_path, answer_key_path, status, is_deleted,
                source_paper_path, source_paper_sha256, curriculum_volume_id,
                updated_at
            )
            VALUES (?, ?, ?, 'created', 0, ?, ?, ?, datetime('now','localtime'))
            """,
            (
                session_name,
                rubric_path,
                answer_key_path,
                source_path or None,
                source_sha256 or None,
                str(curriculum_volume_id or "").strip() or None,
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

    def update_grading_session(
        self,
        session_id: int,
        *,
        name: str | None = None,
        curriculum_volume_id: str | None = None,
        curriculum_volume_provided: bool = False,
    ) -> None:
        assignments: list[str] = []
        parameters: list[Any] = []
        if name is not None:
            assignments.append("session_name = ?")
            parameters.append(str(name))
        if curriculum_volume_provided:
            assignments.append("curriculum_volume_id = ?")
            parameters.append(str(curriculum_volume_id or "").strip() or None)
        if not assignments:
            raise ValueError("at least one session field must be updated")
        assignments.append("updated_at = datetime('now','localtime')")
        parameters.append(int(session_id))
        self.session.connection.execute(
            f"UPDATE grading_sessions SET {', '.join(assignments)} WHERE id = ?",
            tuple(parameters),
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
                   curriculum_volume_id,
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
                   curriculum_volume_id,
                   created_at, updated_at
            FROM grading_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def session_active_work_counts(self, session_id: int) -> dict[str, int]:
        active_grading_runs = int(
            self.session.connection.execute(
                """
                SELECT COUNT(*)
                FROM grading_runs
                WHERE session_id = ?
                  AND state IN ('running', 'pause_requested', 'paused')
                """,
                (int(session_id),),
            ).fetchone()[0]
        )
        active_jobs = int(
            self.session.connection.execute(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE status IN ('queued', 'running', 'paused')
                  AND json_valid(payload_json) = 1
                  AND json_type(payload_json, '$.session_id') IN ('integer', 'text')
                  AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                """,
                (str(int(session_id)),),
            ).fetchone()[0]
        )
        return {
            "active_jobs": active_jobs,
            "active_grading_runs": active_grading_runs,
        }

    def session_permanent_deletion_counts(self, session_id: int) -> dict[str, int]:
        clean_session_id = int(session_id)
        connection = self.session.connection

        def scalar(statement: str, parameters: tuple[Any, ...]) -> int:
            return max(0, int(connection.execute(statement, parameters).fetchone()[0]))

        return {
            "answer_sheets": scalar(
                "SELECT COUNT(*) FROM exam_papers WHERE session_id = ?",
                (clean_session_id,),
            ),
            "grading_results": scalar(
                "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
                (clean_session_id,),
            ),
            "grading_details": scalar(
                """
                SELECT COUNT(*)
                FROM session_details
                WHERE result_id IN (
                    SELECT id FROM session_results WHERE session_id = ?
                )
                """,
                (clean_session_id,),
            ),
            "annotations": scalar(
                "SELECT COUNT(*) FROM annotated_results WHERE session_id = ?",
                (clean_session_id,),
            ),
            "attendance_rows": scalar(
                "SELECT COUNT(*) FROM session_attendance WHERE session_id = ?",
                (clean_session_id,),
            ),
            "grading_runs": scalar(
                "SELECT COUNT(*) FROM grading_runs WHERE session_id = ?",
                (clean_session_id,),
            ),
            "grading_run_items": scalar(
                """
                SELECT COUNT(*)
                FROM grading_run_items
                WHERE run_id IN (
                    SELECT id FROM grading_runs WHERE session_id = ?
                )
                """,
                (clean_session_id,),
            ),
            "jobs": scalar(
                """
                SELECT COUNT(*)
                FROM jobs
                WHERE json_valid(payload_json) = 1
                  AND json_type(payload_json, '$.session_id') IN ('integer', 'text')
                  AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                """,
                (str(clean_session_id),),
            ),
            "answer_regions": scalar(
                "SELECT COUNT(*) FROM answer_regions WHERE session_id = ?",
                (clean_session_id,),
            ),
            "templates": scalar(
                "SELECT COUNT(*) FROM session_templates WHERE session_id = ?",
                (clean_session_id,),
            ),
        }

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

    def delete_session_attendance(self, session_id: int) -> int:
        cursor = self.session.connection.execute(
            "DELETE FROM session_attendance WHERE session_id = ?",
            (int(session_id),),
        )
        return max(0, int(cursor.rowcount))


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
        curriculum_volume_id: str | None = None,
    ) -> int:
        with self._sessions.session() as session:
            with session.transaction():
                return SessionRepository(session).create_grading_session(
                    session_name,
                    rubric_path,
                    answer_key_path,
                    source_paper_path=source_paper_path,
                    source_paper_sha256=source_paper_sha256,
                    curriculum_volume_id=curriculum_volume_id,
                )

    def rename_grading_session(self, session_id: int, new_name: str) -> None:
        self._write("rename_grading_session", session_id, new_name)

    def update_grading_session(
        self,
        session_id: int,
        *,
        name: str | None = None,
        curriculum_volume_id: str | None = None,
        curriculum_volume_provided: bool = False,
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                SessionRepository(session).update_grading_session(
                    session_id,
                    name=name,
                    curriculum_volume_id=curriculum_volume_id,
                    curriculum_volume_provided=curriculum_volume_provided,
                )

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

    def session_deletion_impact(self, session_id: int) -> dict[str, Any]:
        with self._sessions.session(read_only=True) as session:
            with session.transaction():
                repository = SessionRepository(session)
                current = repository.get_grading_session(int(session_id))
                if current is None:
                    raise ValueError(f"grading session not found: {session_id}")
                active = repository.session_active_work_counts(int(session_id))
                permanent_counts = repository.session_permanent_deletion_counts(
                    int(session_id)
                )
        return {
            "session": current,
            "revision": session_deletion_revision(current),
            **active,
            "permanent_counts": permanent_counts,
        }

    def soft_delete_grading_session_protected(
        self,
        session_id: int,
        *,
        expected_revision: str,
        confirmation_name: str,
    ) -> dict[str, Any]:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                repository = SessionRepository(session)
                current = repository.get_grading_session(int(session_id))
                if current is None:
                    raise ValueError(f"grading session not found: {session_id}")
                if str(confirmation_name) != str(current["session_name"]):
                    raise SessionDeletionConfirmationMismatch(
                        "Session name confirmation does not match"
                    )
                if bool(int(current.get("is_deleted") or 0)):
                    return current
                if session_deletion_revision(current) != str(expected_revision):
                    raise SessionDeletionRevisionConflict(
                        "Session changed after delete preview"
                    )
                active = repository.session_active_work_counts(int(session_id))
                if active["active_jobs"] or active["active_grading_runs"]:
                    raise SessionDeletionActiveWork(**active)
                repository.soft_delete_grading_session(int(session_id))
                deleted = repository.get_grading_session(int(session_id))
                if deleted is None:
                    raise RuntimeError("Session disappeared while being archived")
                return deleted

    def assert_permanent_deletion_ready(
        self,
        session_id: int,
        *,
        expected_revision: str,
        confirmation_phrase: str,
    ) -> dict[str, Any]:
        with self._sessions.session(read_only=True) as session:
            with session.transaction():
                repository = SessionRepository(session)
                current = repository.get_grading_session(int(session_id))
                if current is None:
                    raise ValueError(f"grading session not found: {session_id}")
                expected_phrase = f"永久删除 {current['session_name']}"
                if str(confirmation_phrase) != expected_phrase:
                    raise SessionDeletionConfirmationMismatch(
                        "Permanent deletion phrase does not match"
                    )
                if not bool(int(current.get("is_deleted") or 0)):
                    raise SessionPermanentDeletionRequiresArchive(
                        "Session must be archived before permanent deletion"
                    )
                if session_deletion_revision(current) != str(expected_revision):
                    raise SessionDeletionRevisionConflict(
                        "Session changed after permanent deletion preview"
                    )
                active = repository.session_active_work_counts(int(session_id))
                if active["active_jobs"] or active["active_grading_runs"]:
                    raise SessionDeletionActiveWork(**active)
                return current

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
