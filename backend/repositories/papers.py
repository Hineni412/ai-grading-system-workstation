"""Exam-paper persistence and status queries."""

from __future__ import annotations

from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider
from backend.status_contracts import validate_status


class PaperRepository:
    """Session-bound exam-paper SQL without transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

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
        match_status = validate_status(
            "exam_papers.match_status",
            match_status,
        )
        processing_status = validate_status(
            "exam_papers.processing_status",
            processing_status,
        )
        cursor = self.session.connection.execute(
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
        return int(cursor.lastrowid)

    def update_exam_paper_status(
        self,
        paper_id: int,
        processing_status: str,
        error_message: str | None = None,
    ) -> None:
        processing_status = validate_status(
            "exam_papers.processing_status",
            processing_status,
        )
        self.session.connection.execute(
            """
            UPDATE exam_papers
            SET processing_status = ?, error_message = ?
            WHERE id = ?
            """,
            (processing_status, error_message, paper_id),
        )

    def update_exam_paper_status_if_current_assignment(
        self,
        paper_id: int,
        student_id: int,
        processing_status: str,
        error_message: str | None = None,
    ) -> bool:
        processing_status = validate_status(
            "exam_papers.processing_status",
            processing_status,
        )
        cursor = self.session.connection.execute(
            """
            UPDATE exam_papers
            SET processing_status = ?, error_message = ?
            WHERE id = ?
              AND student_id = ?
              AND match_status = 'matched'
            """,
            (processing_status, error_message, paper_id, student_id),
        )
        return cursor.rowcount == 1

    def get_paper_statuses(
        self,
        paper_ids: list[int],
    ) -> list[dict[str, Any]]:
        normalized_ids = [int(value) for value in paper_ids]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        rows = self.session.connection.execute(
            f"""
            SELECT id, processing_status, error_message
            FROM exam_papers
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            normalized_ids,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_session_paper_identities(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT id, front_image, back_image, student_id, match_status
            FROM exam_papers
            WHERE session_id = ?
            ORDER BY id
            """,
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_session_progress_source(
        self,
        session_id: int,
    ) -> dict[str, int]:
        counts = self.session.connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END) AS matched,
                SUM(CASE WHEN match_status <> 'matched' THEN 1 ELSE 0 END) AS unmatched,
                SUM(CASE WHEN processing_status = 'graded' THEN 1 ELSE 0 END) AS graded,
                SUM(CASE WHEN processing_status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN processing_status = 'grading' THEN 1 ELSE 0 END) AS in_progress
            FROM exam_papers
            WHERE session_id = ?
            """,
            (int(session_id),),
        ).fetchone()
        review_count = self.session.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM session_results
            WHERE session_id = ? AND needs_human_review = 1
            """,
            (int(session_id),),
        ).fetchone()
        attendance = self.session.connection.execute(
            """
            SELECT
                SUM(CASE WHEN attendance_status = 'absent' THEN 1 ELSE 0 END) AS absent,
                SUM(CASE WHEN attendance_status = 'scan_issue' THEN 1 ELSE 0 END) AS scan_issue
            FROM session_attendance
            WHERE session_id = ?
            """,
            (int(session_id),),
        ).fetchone()
        return {
            "total": int(counts["total"] or 0),
            "matched": int(counts["matched"] or 0),
            "unmatched": int(counts["unmatched"] or 0),
            "graded": int(counts["graded"] or 0),
            "failed": int(counts["failed"] or 0),
            "in_progress": int(counts["in_progress"] or 0),
            "review_count": int(review_count["count"] or 0),
            "absent": int(attendance["absent"] or 0),
            "scan_issue": int(attendance["scan_issue"] or 0),
        }

    def get_failed_paper_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT
                ep.id AS paper_id,
                ep.ocr_name,
                COALESCE(s.name, ep.ocr_name, '未知') AS student_name,
                s.student_code,
                s.class_name,
                ep.processing_status,
                CASE
                    WHEN ep.processing_status = 'failed' THEN ep.error_message
                    WHEN ep.processing_status = 'grading'
                        THEN '批改任务异常中断，需重新批改'
                    ELSE 'AI批改部分大题缺失，需重新发AI批改'
                END AS error_message,
                ep.created_at
            FROM exam_papers ep
            LEFT JOIN students s ON s.id = ep.student_id
            LEFT JOIN grading_sessions gs ON gs.id = ep.session_id
            WHERE ep.session_id = ?
              AND (
                ep.processing_status = 'failed'
                OR (
                  ep.processing_status = 'grading'
                  AND COALESCE(gs.status, '') <> 'running'
                )
                OR (
                  ep.processing_status = 'graded'
                  AND EXISTS (
                    SELECT 1
                    FROM session_results sr
                    WHERE sr.paper_id = ep.id
                      AND (
                        sr.raw_json LIKE '%"hybrid_batch_fallback"%'
                        OR CASE
                          WHEN json_valid(sr.raw_json)
                          THEN json_extract(
                            sr.raw_json,
                            '$.grading_completeness.status'
                          )
                        END IN ('incomplete', 'invalid')
                      )
                  )
                )
              )
            ORDER BY ep.id ASC
            """,
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_failed_paper_detail_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT
                ep.id AS paper_id,
                ep.front_image,
                ep.back_image,
                ep.ocr_name,
                ep.student_id,
                ep.match_status
            FROM exam_papers ep
            LEFT JOIN grading_sessions gs ON gs.id = ep.session_id
            WHERE ep.session_id = ?
              AND (
                ep.processing_status = 'failed'
                OR (
                  ep.processing_status = 'grading'
                  AND COALESCE(gs.status, '') <> 'running'
                )
                OR (
                  ep.processing_status = 'graded'
                  AND EXISTS (
                    SELECT 1
                    FROM session_results sr
                    WHERE sr.paper_id = ep.id
                      AND (
                        sr.raw_json LIKE '%"hybrid_batch_fallback"%'
                        OR CASE
                          WHEN json_valid(sr.raw_json)
                          THEN json_extract(
                            sr.raw_json,
                            '$.grading_completeness.status'
                          )
                        END IN ('incomplete', 'invalid')
                      )
                  )
                )
              )
            ORDER BY ep.id ASC
            """,
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_session_anomaly_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT
                'unmatched_paper:' || printf('%020d', ep.id) AS anomaly_id,
                'unmatched_paper' AS anomaly_type,
                '未匹配试卷 #' || ep.id AS display_name,
                NULL AS student_code,
                NULL AS class_name,
                ep.match_status AS status,
                NULL AS detail,
                ep.created_at AS created_at
            FROM exam_papers ep
            WHERE ep.session_id = ? AND ep.match_status <> 'matched'

            UNION ALL

            SELECT
                'scan_issue:' || printf('%020d', sa.id) AS anomaly_id,
                'scan_issue' AS anomaly_type,
                '扫描异常记录 #' || sa.id AS display_name,
                s.student_code AS student_code,
                s.class_name AS class_name,
                sa.attendance_status AS status,
                sa.source_reason AS detail,
                sa.created_at AS created_at
            FROM session_attendance sa
            JOIN students s ON s.id = sa.student_id
            WHERE sa.session_id = ? AND sa.attendance_status = 'scan_issue'

            ORDER BY anomaly_type, anomaly_id
            """,
            (int(session_id), int(session_id)),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_session_storage_path_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT front_image, back_image
            FROM exam_papers
            WHERE session_id = ?
            """,
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_grading_papers_failed(
        self,
        session_id: int,
        error_message: str,
    ) -> int:
        cursor = self.session.connection.execute(
            """
            UPDATE exam_papers
            SET processing_status = 'failed', error_message = ?
            WHERE session_id = ? AND processing_status = 'grading'
            """,
            (str(error_message), int(session_id)),
        )
        return max(0, int(cursor.rowcount))

    def delete_session_papers(self, session_id: int) -> int:
        cursor = self.session.connection.execute(
            "DELETE FROM exam_papers WHERE session_id = ?",
            (int(session_id),),
        )
        return max(0, int(cursor.rowcount))


class PaperRepositoryGateway:
    """Open one owned or borrowed repository session per paper operation."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

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
        with self._sessions.session() as session:
            with session.transaction():
                return PaperRepository(session).create_exam_paper(
                    session_id,
                    front_image,
                    back_image,
                    ocr_name,
                    student_id,
                    match_status,
                    processing_status,
                    error_message,
                )

    def update_exam_paper_status(
        self,
        paper_id: int,
        processing_status: str,
        error_message: str | None = None,
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                PaperRepository(session).update_exam_paper_status(
                    paper_id,
                    processing_status,
                    error_message,
                )

    def update_exam_paper_status_if_current_assignment(
        self,
        paper_id: int,
        student_id: int,
        processing_status: str,
        error_message: str | None = None,
    ) -> bool:
        with self._sessions.session() as session:
            with session.transaction():
                return PaperRepository(
                    session
                ).update_exam_paper_status_if_current_assignment(
                    paper_id,
                    student_id,
                    processing_status,
                    error_message,
                )

    def get_paper_statuses(
        self,
        paper_ids: list[int],
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return PaperRepository(session).get_paper_statuses(paper_ids)

    def get_session_paper_identities(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return PaperRepository(session).get_session_paper_identities(
                session_id
            )

    def get_session_progress_source(
        self,
        session_id: int,
    ) -> dict[str, int]:
        with self._sessions.session(read_only=True) as session:
            return PaperRepository(session).get_session_progress_source(
                session_id
            )

    def get_failed_paper_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return PaperRepository(session).get_failed_paper_rows(session_id)

    def get_failed_paper_detail_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return PaperRepository(session).get_failed_paper_detail_rows(
                session_id
            )

    def get_session_anomaly_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return PaperRepository(session).get_session_anomaly_rows(
                session_id
            )

    def get_session_storage_path_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return PaperRepository(session).get_session_storage_path_rows(
                session_id
            )
