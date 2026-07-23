"""Consistent read snapshots for session report exports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider


@dataclass(frozen=True, slots=True)
class SessionReportSnapshot:
    session: dict[str, Any] | None
    results: list[dict[str, Any]]
    attendance: list[dict[str, Any]]
    details: list[dict[str, Any]]


class ReportRepository:
    """Build one report projection from one repository transaction."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    def get_session_rubric_path(self, session_id: int) -> str | None:
        row = self._session.connection.execute(
            "SELECT rubric_path FROM grading_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        if row is None or not row["rubric_path"]:
            return None
        return str(row["rubric_path"])

    def session_report_snapshot(self, session_id: int) -> SessionReportSnapshot:
        connection = self._session.connection
        session_row = connection.execute(
            """
            SELECT id, session_name, rubric_path
            FROM grading_sessions
            WHERE id = ?
            """,
            (int(session_id),),
        ).fetchone()
        result_rows = connection.execute(
            """
            SELECT
                sr.id AS result_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sr.total_score,
                sr.student_score,
                sr.needs_human_review,
                sr.graded_at,
                sr.raw_json
            FROM session_results sr
            JOIN students s ON s.id = sr.student_id
            WHERE sr.session_id = ?
            ORDER BY sr.id ASC
            """,
            (int(session_id),),
        ).fetchall()
        attendance_rows = connection.execute(
            """
            SELECT
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sa.attendance_status,
                sa.source_reason,
                sa.created_at
            FROM session_attendance sa
            JOIN students s ON s.id = sa.student_id
            WHERE sa.session_id = ?
            ORDER BY s.class_name ASC, s.student_code ASC, s.name ASC
            """,
            (int(session_id),),
        ).fetchall()
        detail_rows = connection.execute(
            """
            SELECT
                sr.id AS result_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sd.question_id,
                sd.score_awarded,
                sd.deduction_reason,
                sd.knowledge_id,
                sd.knowledge_ids,
                sd.error_category,
                sd.error_summary
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN students s ON s.id = sr.student_id
            WHERE sr.session_id = ?
            ORDER BY sr.id ASC, sd.id ASC
            """,
            (int(session_id),),
        ).fetchall()
        return SessionReportSnapshot(
            session=dict(session_row) if session_row is not None else None,
            results=[dict(row) for row in result_rows],
            attendance=[dict(row) for row in attendance_rows],
            details=[dict(row) for row in detail_rows],
        )


class ReportRepositoryGateway:
    """Open one consistent read transaction per report snapshot."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

    def get_session_report_snapshot(
        self,
        session_id: int,
    ) -> SessionReportSnapshot:
        with self._sessions.session(read_only=True) as session:
            with session.transaction():
                return ReportRepository(session).session_report_snapshot(session_id)

    def get_session_rubric_path(self, session_id: int) -> str | None:
        with self._sessions.session(read_only=True) as session:
            return ReportRepository(session).get_session_rubric_path(session_id)


__all__ = [
    "ReportRepository",
    "ReportRepositoryGateway",
    "SessionReportSnapshot",
]
