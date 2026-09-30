"""Grading-session and attendance persistence boundaries."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider
from backend.repositories.papers import PaperRepository
from backend.repositories.results import ResultRepository
from backend.repositories.review import ReviewRepository
from backend.repositories.templates import RegionRepository, TemplateRepository
from backend.status_contracts import validate_status

QUESTION_BANK_SYNC_STATES = {"not_started", "running", "ready", "partial", "failed"}


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


class SessionNameConflict(ValueError):
    """Another exam already owns the normalized display name."""


def _clean_session_name(value: object) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError("session name must be nonblank")
    return clean


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
        clean_name = _clean_session_name(session_name)
        conflict = self.session.connection.execute(
            "SELECT 1 FROM grading_sessions WHERE lower(trim(session_name)) = lower(?) LIMIT 1",
            (clean_name,),
        ).fetchone()
        if conflict is not None:
            raise SessionNameConflict("An exam with this name already exists")
        source_path, source_sha256 = _validated_source_binding(
            source_paper_path,
            source_paper_sha256,
        )
        try:
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
                    clean_name,
                    rubric_path,
                    answer_key_path,
                    source_path or None,
                    source_sha256 or None,
                    str(curriculum_volume_id or "").strip() or None,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "session_name_conflict" in str(exc):
                raise SessionNameConflict("An exam with this name already exists") from exc
            raise
        return int(cursor.lastrowid)

    def rename_grading_session(self, session_id: int, new_name: str) -> None:
        self._update_session_name(session_id, new_name)

    def _update_session_name(self, session_id: int, new_name: str) -> None:
        clean_name = _clean_session_name(new_name)
        conflict = self.session.connection.execute(
            """
            SELECT 1 FROM grading_sessions
            WHERE id <> ? AND lower(trim(session_name)) = lower(?)
            LIMIT 1
            """,
            (int(session_id), clean_name),
        ).fetchone()
        if conflict is not None:
            raise SessionNameConflict("An exam with this name already exists")
        try:
            self.session.connection.execute(
                """
                UPDATE grading_sessions
                SET session_name = ?, updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (clean_name, session_id),
            )
        except sqlite3.IntegrityError as exc:
            if "session_name_conflict" in str(exc):
                raise SessionNameConflict("An exam with this name already exists") from exc
            raise

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
            clean_name = _clean_session_name(name)
            conflict = self.session.connection.execute(
                """
                SELECT 1 FROM grading_sessions
                WHERE id <> ? AND lower(trim(session_name)) = lower(?)
                LIMIT 1
                """,
                (int(session_id), clean_name),
            ).fetchone()
            if conflict is not None:
                raise SessionNameConflict("An exam with this name already exists")
            assignments.append("session_name = ?")
            parameters.append(clean_name)
        if curriculum_volume_provided:
            assignments.append("curriculum_volume_id = ?")
            parameters.append(str(curriculum_volume_id or "").strip() or None)
        if not assignments:
            raise ValueError("at least one session field must be updated")
        assignments.append("updated_at = datetime('now','localtime')")
        parameters.append(int(session_id))
        try:
            self.session.connection.execute(
                f"UPDATE grading_sessions SET {', '.join(assignments)} WHERE id = ?",
                tuple(parameters),
            )
        except sqlite3.IntegrityError as exc:
            if "session_name_conflict" in str(exc):
                raise SessionNameConflict("An exam with this name already exists") from exc
            raise

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

    def __init__(
        self,
        sessions: RepositorySessionProvider,
        *,
        db_path: Path | None = None,
        create_backup: Callable[[str], Any] | None = None,
        invalidate_rubric_maps: Callable[[int], None] | None = None,
    ) -> None:
        self._sessions = sessions
        self._db_path = (
            Path(db_path)
            if db_path is not None
            else getattr(sessions, "database", None)
        )
        self._create_backup = create_backup
        self._invalidate_rubric_maps = invalidate_rubric_maps

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

    def bind_grading_session_source(
        self,
        session_id: int,
        *,
        source_paper_path: str,
        source_paper_sha256: str,
    ) -> None:
        source_path, source_sha256 = _validated_source_binding(
            source_paper_path,
            source_paper_sha256,
        )
        with self._sessions.session() as session:
            with session.transaction():
                conn = session.connection
                current = conn.execute(
                    "SELECT source_paper_sha256 FROM grading_sessions WHERE id = ?",
                    (int(session_id),),
                ).fetchone()
                if current is None:
                    raise KeyError(f"grading session not found: {session_id}")
                changed = str(current["source_paper_sha256"] or "") != source_sha256
                conn.execute(
                    """
                    UPDATE grading_sessions
                    SET source_paper_path = ?, source_paper_sha256 = ?,
                        question_bank_sync_state = CASE WHEN ? THEN 'not_started' ELSE question_bank_sync_state END,
                        question_bank_sync_details_json = CASE WHEN ? THEN '{}' ELSE question_bank_sync_details_json END,
                        question_bank_sync_error = CASE WHEN ? THEN NULL ELSE question_bank_sync_error END,
                        question_bank_sync_updated_at = CASE WHEN ? THEN NULL ELSE question_bank_sync_updated_at END,
                        updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (
                        source_path,
                        source_sha256,
                        changed,
                        changed,
                        changed,
                        changed,
                        int(session_id),
                    ),
                )

    def update_question_bank_sync_state(
        self,
        session_id: int,
        *,
        state: str,
        details: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        normalized = str(state or "").strip().casefold()
        if normalized not in QUESTION_BANK_SYNC_STATES:
            raise ValueError(f"unsupported question-bank sync state: {state}")
        with self._sessions.session() as session:
            with session.transaction():
                cursor = session.connection.execute(
                    """
                    UPDATE grading_sessions
                    SET question_bank_sync_state = ?, question_bank_sync_details_json = ?,
                        question_bank_sync_error = ?,
                        question_bank_sync_updated_at = datetime('now','localtime'),
                        updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (
                        normalized,
                        json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True),
                        str(error).strip() if error else None,
                        int(session_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"grading session not found: {session_id}")

    def update_grading_session_config(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        template_config_path: str | None = None,
    ) -> None:
        if self._invalidate_rubric_maps is not None:
            self._invalidate_rubric_maps(session_id)
        with self._sessions.session() as session:
            with session.transaction():
                session.connection.execute(
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

    def publish_grading_session_config(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        expected_rubric_path: str,
        expected_answer_key_path: str,
        preserve_question_bank_sync: bool = False,
    ) -> bool:
        config_changed = (
            str(rubric_path) != str(expected_rubric_path)
            or str(answer_key_path) != str(expected_answer_key_path)
        )
        # Editor saves keep the same question set and re-annotate evidence
        # ids, so confirmed bank links stay valid and the sync state is kept.
        reset_sync = config_changed and not preserve_question_bank_sync
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                conn = session.connection
                cursor = conn.execute(
                    """
                    UPDATE grading_sessions
                    SET rubric_path = ?, answer_key_path = ?,
                        question_bank_sync_state = CASE
                            WHEN ? THEN 'not_started' ELSE question_bank_sync_state END,
                        question_bank_sync_details_json = CASE
                            WHEN ? THEN '{}' ELSE question_bank_sync_details_json END,
                        question_bank_sync_error = CASE
                            WHEN ? THEN NULL ELSE question_bank_sync_error END,
                        question_bank_sync_updated_at = CASE
                            WHEN ? THEN NULL ELSE question_bank_sync_updated_at END,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                      AND rubric_path = ? AND answer_key_path = ?
                    """,
                    (
                        str(rubric_path),
                        str(answer_key_path),
                        reset_sync,
                        reset_sync,
                        reset_sync,
                        reset_sync,
                        int(session_id),
                        str(expected_rubric_path),
                        str(expected_answer_key_path),
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                conn.execute(
                    """
                    UPDATE session_templates
                    SET is_confirmed = 0,
                        regions_snapshot_pending = 0,
                        regions_snapshot_token = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE session_id = ?
                    """,
                    (int(session_id),),
                )
        if self._invalidate_rubric_maps is not None:
            self._invalidate_rubric_maps(int(session_id))
        return True

    def publish_grading_session_config_with_source(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        source_paper_path: str,
        source_paper_sha256: str,
        expected_rubric_path: str,
        expected_answer_key_path: str,
    ) -> bool:
        source_path, source_sha256 = _validated_source_binding(
            source_paper_path,
            source_paper_sha256,
        )
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                conn = session.connection
                current = conn.execute(
                    "SELECT rubric_path, answer_key_path, source_paper_sha256 "
                    "FROM grading_sessions "
                    "WHERE id = ? AND is_deleted = 0",
                    (int(session_id),),
                ).fetchone()
                if current is None:
                    return False
                changed = (
                    str(current["source_paper_sha256"] or "") != source_sha256
                    or str(current["rubric_path"] or "") != str(rubric_path)
                    or str(current["answer_key_path"] or "") != str(answer_key_path)
                )
                cursor = conn.execute(
                    """
                    UPDATE grading_sessions
                    SET rubric_path = ?, answer_key_path = ?,
                        source_paper_path = ?, source_paper_sha256 = ?,
                        question_bank_sync_state = CASE
                            WHEN ? THEN 'not_started' ELSE question_bank_sync_state END,
                        question_bank_sync_details_json = CASE
                            WHEN ? THEN '{}' ELSE question_bank_sync_details_json END,
                        question_bank_sync_error = CASE
                            WHEN ? THEN NULL ELSE question_bank_sync_error END,
                        question_bank_sync_updated_at = CASE
                            WHEN ? THEN NULL ELSE question_bank_sync_updated_at END,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                      AND rubric_path = ? AND answer_key_path = ?
                    """,
                    (
                        str(rubric_path),
                        str(answer_key_path),
                        source_path,
                        source_sha256,
                        changed,
                        changed,
                        changed,
                        changed,
                        int(session_id),
                        str(expected_rubric_path),
                        str(expected_answer_key_path),
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                conn.execute(
                    """
                    UPDATE session_templates
                    SET is_confirmed = 0,
                        regions_snapshot_pending = 0,
                        regions_snapshot_token = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE session_id = ?
                    """,
                    (int(session_id),),
                )
        if self._invalidate_rubric_maps is not None:
            self._invalidate_rubric_maps(int(session_id))
        return True

    def collect_session_storage_paths(self, session_id: int) -> list[str]:
        paths: list[str] = []

        def add_values(row: Any, fields: list[str]) -> None:
            if row is None:
                return
            for field in fields:
                value = row[field]
                if value:
                    paths.append(str(value))

        with self._sessions.session(read_only=True) as session:
            conn = session.connection
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
                TemplateRepository(session).get_session_storage_path_row(session_id),
                [
                    "front_template_path",
                    "back_template_path",
                    "ai_analysis_path",
                    "template_config_path",
                    "regions_path",
                ],
            )
            for row in PaperRepository(session).get_session_storage_path_rows(
                session_id
            ):
                add_values(row, ["front_image", "back_image"])
            for row in ReviewRepository(session).get_session_annotation_path_rows(
                session_id
            ):
                add_values(row, ["annotated_front_path", "annotated_back_path"])
            report_rows = conn.execute(
                """
                SELECT json_extract(result_json, '$.file_path') AS file_path
                FROM jobs
                WHERE job_type = 'report_export'
                  AND json_valid(payload_json) = 1
                  AND json_type(payload_json, '$.session_id') IN ('integer', 'text')
                  AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                  AND json_valid(result_json) = 1
                  AND json_type(result_json, '$.file_path') = 'text'
                """,
                (str(int(session_id)),),
            ).fetchall()
            config_job_rows = conn.execute(
                """
                SELECT id, json_extract(payload_json, '$.input_id') AS input_id
                FROM jobs
                WHERE job_type = 'config_generation'
                  AND json_valid(payload_json) = 1
                  AND json_type(payload_json, '$.session_id') IN ('integer', 'text')
                  AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                """,
                (str(int(session_id)),),
            ).fetchall()
        for row in report_rows:
            add_values(row, ["file_path"])
        upload_config_dir = self._db_path.parent.parent / "config" / "uploaded"
        for row in config_job_rows:
            job_id = int(row["id"])
            paths.append(
                str(upload_config_dir / f"config_generation_draft_job_{job_id}.json")
            )
            input_id = str(row["input_id"] or "").strip().casefold()
            if re.fullmatch(r"[0-9a-f]{32}", input_id):
                paths.append(
                    str(upload_config_dir / f"config_generation_input_{input_id}.json")
                )

        return paths

    def collect_session_reupload_storage_paths(self, session_id: int) -> list[str]:
        """Return only files produced from uploaded student answer sheets."""
        paths: list[str] = []

        def add_values(row: Any, fields: list[str]) -> None:
            if row is None:
                return
            for field in fields:
                value = row[field]
                if value:
                    paths.append(str(value))

        with self._sessions.session(read_only=True) as session:
            conn = session.connection
            for row in PaperRepository(session).get_session_storage_path_rows(session_id):
                add_values(row, ["front_image", "back_image"])
            for row in ReviewRepository(session).get_session_annotation_path_rows(session_id):
                add_values(row, ["annotated_front_path", "annotated_back_path"])
            rows = conn.execute(
                """
                SELECT json_extract(result_json, '$.file_path') AS file_path
                FROM jobs
                WHERE job_type = 'report_export'
                  AND json_valid(payload_json) = 1
                  AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                  AND json_valid(result_json) = 1
                  AND json_type(result_json, '$.file_path') = 'text'
                """,
                (str(int(session_id)),),
            ).fetchall()
        for row in rows:
            add_values(row, ["file_path"])
        return paths

    def reset_session_for_scan_replacement(self, session_id: int) -> dict[str, int]:
        """Delete every downstream result while preserving exam setup and roster."""
        counts: dict[str, int] = {}
        with self._sessions.session() as repository_session:
            with repository_session.transaction(immediate=True):
                results = ResultRepository(repository_session)
                reviews = ReviewRepository(repository_session)
                papers = PaperRepository(repository_session)
                sessions = SessionRepository(repository_session)
                active = sessions.session_active_work_counts(int(session_id))
                if active["active_jobs"] or active["active_grading_runs"]:
                    raise SessionDeletionActiveWork(**active)
                connection = repository_session.connection
                counts["grading_run_items"] = max(0, int(connection.execute(
                    """
                    DELETE FROM grading_run_items
                    WHERE run_id IN (SELECT id FROM grading_runs WHERE session_id = ?)
                    """,
                    (int(session_id),),
                ).rowcount))
                counts["grading_runs"] = max(0, int(connection.execute(
                    "DELETE FROM grading_runs WHERE session_id = ?",
                    (int(session_id),),
                ).rowcount))
                counts["jobs"] = max(0, int(connection.execute(
                    """
                    DELETE FROM jobs
                    WHERE job_type IN ('scan_analysis', 'grading_run', 'report_export')
                      AND json_valid(payload_json) = 1
                      AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                    """,
                    (str(int(session_id)),),
                ).rowcount))
                counts["teacher_score_locks"] = reviews.delete_session_teacher_score_locks(
                    session_id
                )
                result_ids = results.get_session_result_ids(session_id)
                counts["session_details"] = results.delete_result_details(result_ids)
                counts["annotated_results"] = reviews.delete_session_annotations(
                    session_id, result_ids
                )
                counts["session_attendance"] = sessions.delete_session_attendance(session_id)
                counts["session_results"] = results.delete_session_results(session_id)
                counts["exam_papers"] = papers.delete_session_papers(session_id)
                connection.execute(
                    """
                    UPDATE grading_sessions
                    SET status = 'created', updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (int(session_id),),
                )
        return counts

    def hard_delete_grading_session(
        self,
        session_id: int,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, int]:
        counts = {
            "teacher_score_locks": 0,
            "grading_run_items": 0,
            "grading_runs": 0,
            "jobs": 0,
            "session_details": 0,
            "annotated_results": 0,
            "session_attendance": 0,
            "session_results": 0,
            "exam_papers": 0,
            "answer_regions": 0,
            "session_templates": 0,
            "grading_sessions": 0,
        }

        with self._sessions.session() as repository_session:
            with repository_session.transaction(immediate=True):
                results = ResultRepository(repository_session)
                reviews = ReviewRepository(repository_session)
                papers = PaperRepository(repository_session)
                sessions = SessionRepository(repository_session)
                regions = RegionRepository(repository_session)
                templates = TemplateRepository(repository_session)
                current = sessions.get_grading_session(int(session_id))
                if current is None:
                    raise ValueError(f"Session {session_id} does not exist.")
                if (
                    expected_revision is not None
                    and session_deletion_revision(current) != str(expected_revision)
                ):
                    raise SessionDeletionRevisionConflict(
                        "Session changed before permanent deletion"
                    )
                active = sessions.session_active_work_counts(int(session_id))
                if active["active_jobs"] or active["active_grading_runs"]:
                    raise SessionDeletionActiveWork(**active)
                counts["grading_run_items"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            """
                            DELETE FROM grading_run_items
                            WHERE run_id IN (
                                SELECT id FROM grading_runs WHERE session_id = ?
                            )
                            """,
                            (int(session_id),),
                        ).rowcount
                    ),
                )
                counts["grading_runs"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            "DELETE FROM grading_runs WHERE session_id = ?",
                            (int(session_id),),
                        ).rowcount
                    ),
                )
                counts["jobs"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            """
                            DELETE FROM jobs
                            WHERE json_valid(payload_json) = 1
                              AND json_type(payload_json, '$.session_id')
                                  IN ('integer', 'text')
                              AND CAST(
                                  json_extract(payload_json, '$.session_id') AS TEXT
                              ) = ?
                            """,
                            (str(int(session_id)),),
                        ).rowcount
                    ),
                )
                counts["teacher_score_locks"] = (
                    reviews.delete_session_teacher_score_locks(session_id)
                )
                result_ids = results.get_session_result_ids(session_id)
                counts["session_details"] = results.delete_result_details(
                    result_ids
                )
                counts["annotated_results"] = (
                    reviews.delete_session_annotations(
                        session_id,
                        result_ids,
                    )
                )
                counts["session_attendance"] = (
                    sessions.delete_session_attendance(session_id)
                )
                counts["session_results"] = results.delete_session_results(
                    session_id
                )
                counts["exam_papers"] = papers.delete_session_papers(
                    session_id
                )
                counts["answer_regions"] = regions.delete_session_regions(
                    session_id
                )
                counts["session_templates"] = (
                    templates.delete_session_templates(session_id)
                )
                counts["grading_sessions"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            """
                            DELETE FROM grading_sessions
                            WHERE id = ?
                            """,
                            (session_id,),
                        ).rowcount
                    ),
                )

        return counts

    def update_session_status(self, session_id: int, status: str) -> None:
        status = validate_status("grading_sessions.status", status)
        with self._sessions.session() as repository_session:
            with repository_session.transaction():
                repository_session.connection.execute(
                    """
                    UPDATE grading_sessions
                    SET status = ?, updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (status, session_id),
                )
                if status != "running":
                    PaperRepository(
                        repository_session
                    ).mark_grading_papers_failed(
                        session_id,
                        "批改中途被终止或强制重置",
                    )

    def try_start_session_run(self, session_id: int) -> bool:
        with self._sessions.session() as repository_session:
            with repository_session.transaction(immediate=True):
                cursor = repository_session.connection.execute(
                    """
                    UPDATE grading_sessions
                    SET status = 'running',
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND COALESCE(status, '') <> 'running'
                    """,
                    (session_id,),
                )
                success = int(cursor.rowcount or 0) == 1
                if success:
                    PaperRepository(
                        repository_session
                    ).mark_grading_papers_failed(
                        session_id,
                        "批改中途被异常中断，请重试",
                    )
                return success

    def finish_session_run(self, session_id: int, status: str = "completed") -> None:
        self.update_session_status(session_id, status)

    def clear_session_run_data(self, session_id: int) -> None:
        self._create_backup("clear_session")
        with self._sessions.session() as repository_session:
            with repository_session.transaction(immediate=True):
                # Teacher-confirmed sidecar scores intentionally survive an
                # ordinary AI run reset and are removed only by hard delete.
                results = ResultRepository(repository_session)
                result_ids = results.get_session_result_ids(session_id)
                results.delete_result_details(result_ids)
                ReviewRepository(
                    repository_session
                ).delete_session_annotations(session_id, result_ids)
                SessionRepository(
                    repository_session
                ).delete_session_attendance(session_id)
                results.delete_session_results(session_id)
                PaperRepository(repository_session).delete_session_papers(
                    session_id
                )
