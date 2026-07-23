"""Grading-result and detail persistence boundaries."""

from __future__ import annotations

import json
from typing import Any

from backend.domain_models import GradingResult, QuestionGradingDetail
from backend.repositories.base import RepositorySession, RepositorySessionProvider
from backend.repositories.papers import PaperRepository
from grading_completeness import audit_grading_details


class ResultRepository:
    """Session-bound result SQL without connection or transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def save_session_result(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
        grading_result: GradingResult,
    ) -> int:
        old_rows = self.session.connection.execute(
            """
            SELECT id
            FROM session_results
            WHERE session_id = ? AND student_id = ?
            """,
            (session_id, student_id),
        ).fetchall()
        for old_row in old_rows:
            old_id = int(old_row["id"])
            self.session.connection.execute(
                "DELETE FROM annotated_results WHERE result_id = ?",
                (old_id,),
            )
            self.session.connection.execute(
                "DELETE FROM session_details WHERE result_id = ?",
                (old_id,),
            )
            self.session.connection.execute(
                "DELETE FROM session_results WHERE id = ?",
                (old_id,),
            )

        cursor = self.session.connection.execute(
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
            self.session.connection.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_id, knowledge_ids, error_category, error_summary,
                    confidence_score, secondary_errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _serialize_secondary_errors(
                        getattr(detail, "secondary_errors", [])
                    ),
                ),
            )
        return result_id

    def current_assignment_matches(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
    ) -> bool:
        row = self.session.connection.execute(
            """
            SELECT 1
            FROM exam_papers
            WHERE id = ?
              AND session_id = ?
              AND student_id = ?
              AND match_status = 'matched'
            """,
            (paper_id, session_id, student_id),
        ).fetchone()
        return row is not None

    def get_session_results(self, session_id: int) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT
                sr.id AS result_id,
                sr.student_id,
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

    def get_result_details(self, result_id: int) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
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
                confidence_score,
                secondary_errors_json
            FROM session_details
            WHERE result_id = ?
            ORDER BY id ASC
            """,
            (result_id,),
        ).fetchall()
        return [_detail_row_with_secondary_errors(dict(row)) for row in rows]

    def get_result_context(
        self,
        result_id: int,
    ) -> dict[str, Any] | None:
        row = self.session.connection.execute(
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

    def get_session_completeness_source(
        self,
        session_id: int,
    ) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
        result_rows = self.session.connection.execute(
            """
            SELECT
                sr.id AS result_id,
                sr.student_id,
                sr.paper_id,
                sr.raw_json,
                ep.front_image,
                ep.back_image,
                ep.ocr_name,
                ep.match_status,
                ep.processing_status,
                ep.error_message,
                s.student_code,
                s.name AS student_name,
                s.class_name
            FROM session_results sr
            JOIN exam_papers ep ON ep.id = sr.paper_id
            JOIN students s ON s.id = sr.student_id
            WHERE sr.session_id = ?
            ORDER BY sr.id ASC
            """,
            (int(session_id),),
        ).fetchall()
        results = [dict(row) for row in result_rows]
        if not results:
            return [], {}

        result_ids = [int(row["result_id"]) for row in results]
        placeholders = ",".join("?" for _ in result_ids)
        detail_rows = self.session.connection.execute(
            f"""
            SELECT
                result_id,
                question_id,
                score_awarded,
                deduction_reason,
                knowledge_id,
                knowledge_ids,
                error_category,
                error_summary,
                confidence_score,
                secondary_errors_json
            FROM session_details
            WHERE result_id IN ({placeholders})
            ORDER BY id ASC
            """,
            result_ids,
        ).fetchall()
        details_by_result: dict[int, list[dict[str, Any]]] = {}
        for row in detail_rows:
            details_by_result.setdefault(
                int(row["result_id"]),
                [],
            ).append(dict(row))
        return results, details_by_result

    def get_session_weak_point_rows(
        self,
        session_id: int,
        student_id: int | None = None,
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
            JOIN students s ON s.id = sr.student_id
            WHERE sr.session_id = ?
        """
        params: list[Any] = [int(session_id)]
        if student_id is not None:
            query += " AND s.id = ?"
            params.append(int(student_id))
        query += " ORDER BY s.id ASC, sd.knowledge_id ASC, sd.id ASC"
        rows = self.session.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_active_weak_point_rows(
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
            params.append(int(student_id))
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            query += f" AND sr.session_id IN ({placeholders})"
            params.extend(int(value) for value in session_ids)
        query += " ORDER BY s.id ASC, sd.knowledge_id ASC, sd.id ASC"
        rows = self.session.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_active_assessment_rows(
        self,
        *,
        student_ids: list[int],
        session_ids: list[int],
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                sr.session_id,
                gs.session_name,
                sr.id AS result_id,
                sr.student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                ep.front_image,
                ep.back_image,
                sd.question_id,
                sd.knowledge_id,
                sd.knowledge_ids,
                sd.score_awarded,
                sd.deduction_reason,
                sd.error_category,
                sd.error_summary,
                sd.secondary_errors_json,
                sr.graded_at
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN grading_sessions gs ON gs.id = sr.session_id
            JOIN students s ON s.id = sr.student_id
            JOIN exam_papers ep ON ep.id = sr.paper_id
            WHERE COALESCE(gs.is_deleted, 0) = 0
        """
        params: list[Any] = []
        if student_ids:
            placeholders = ",".join("?" for _ in student_ids)
            query += f" AND sr.student_id IN ({placeholders})"
            params.extend(int(value) for value in student_ids)
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            query += f" AND sr.session_id IN ({placeholders})"
            params.extend(int(value) for value in session_ids)
        query += " ORDER BY sr.session_id, sr.student_id, sd.id"
        rows = self.session.connection.execute(query, params).fetchall()
        return [
            _detail_row_with_secondary_errors(dict(row))
            for row in rows
        ]

    def get_active_error_point_rows(
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
            params.append(int(student_id))
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            query += f" AND sr.session_id IN ({placeholders})"
            params.extend(int(value) for value in session_ids)
        query += " ORDER BY s.id ASC, sd.id ASC"
        rows = self.session.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_active_student_score_rates(self) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT
                s.id AS student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                ROUND(AVG(
                    CASE
                        WHEN sr.total_score > 0
                        THEN sr.student_score * 100.0 / sr.total_score
                        ELSE 0
                    END
                ), 2) AS avg_score_rate,
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

    def get_active_detail_rows(
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
            placeholders = ",".join("?" for _ in session_ids)
            query += f" AND sr.session_id IN ({placeholders})"
            params.extend(int(value) for value in session_ids)
        query += " ORDER BY sr.graded_at DESC, sd.id ASC"
        rows = self.session.connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_session_result_ids(self, session_id: int) -> list[int]:
        rows = self.session.connection.execute(
            "SELECT id FROM session_results WHERE session_id = ?",
            (int(session_id),),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def delete_result_details(self, result_ids: list[int]) -> int:
        normalized_ids = [int(value) for value in result_ids]
        if not normalized_ids:
            return 0
        placeholders = ",".join("?" for _ in normalized_ids)
        cursor = self.session.connection.execute(
            f"""
            DELETE FROM session_details
            WHERE result_id IN ({placeholders})
            """,
            normalized_ids,
        )
        return max(0, int(cursor.rowcount))

    def delete_session_results(self, session_id: int) -> int:
        cursor = self.session.connection.execute(
            "DELETE FROM session_results WHERE session_id = ?",
            (int(session_id),),
        )
        return max(0, int(cursor.rowcount))

    def replace_result_details_atomic(
        self,
        result_id: int,
        remove_question_ids: list[str],
        replacement_details: list[QuestionGradingDetail],
        *,
        needs_human_review: bool,
        raw_json: dict[str, Any],
        rubric: dict[str, Any] | None = None,
    ) -> None:
        if self.session.connection.execute(
            "SELECT 1 FROM session_results WHERE id = ?",
            (result_id,),
        ).fetchone() is None:
            raise ValueError(f"Unknown session result: {result_id}")

        question_ids = list(
            dict.fromkeys(str(value) for value in remove_question_ids)
        )
        if question_ids:
            placeholders = ",".join("?" for _ in question_ids)
            self.session.connection.execute(
                f"""
                DELETE FROM session_details
                WHERE result_id = ? AND question_id IN ({placeholders})
                """,
                (result_id, *question_ids),
            )
        for detail in replacement_details:
            self._insert_detail(result_id, detail)

        stored_details = [
            dict(row)
            for row in self.session.connection.execute(
                """
                SELECT
                    question_id, score_awarded, deduction_reason, knowledge_id,
                    knowledge_ids, error_category, error_summary,
                    confidence_score, secondary_errors_json
                FROM session_details
                WHERE result_id = ?
                ORDER BY id ASC
                """,
                (result_id,),
            ).fetchall()
        ]
        recalculated_score = sum(
            float(detail["score_awarded"]) for detail in stored_details
        )
        persisted_raw_json = (
            dict(raw_json) if isinstance(raw_json, dict) else {}
        )
        if rubric is not None:
            completeness = audit_grading_details(rubric, stored_details)
            if completeness["status"] != "complete":
                raise ValueError(
                    "Atomic replacement must leave a complete grading result; "
                    f"got {completeness['status']}"
                )
            persisted_raw_json["grading_completeness"] = completeness
        self.session.connection.execute(
            """
            UPDATE session_results
            SET student_score = ?, needs_human_review = ?, raw_json = ?
            WHERE id = ?
            """,
            (
                float(recalculated_score),
                1 if needs_human_review else 0,
                json.dumps(persisted_raw_json, ensure_ascii=False),
                result_id,
            ),
        )

    def record_result_retry_failure(
        self,
        result_id: int,
        attempt: dict[str, Any],
    ) -> None:
        row = self.session.connection.execute(
            "SELECT raw_json FROM session_results WHERE id = ?",
            (result_id,),
        ).fetchone()
        if row is None:
            return
        loaded = _safe_json_loads(row["raw_json"])
        raw_json = loaded if isinstance(loaded, dict) else {}
        attempts = raw_json.get("grading_retry_attempts")
        if not isinstance(attempts, list):
            attempts = []
        attempts.append(dict(attempt))
        raw_json["grading_retry_attempts"] = attempts
        self.session.connection.execute(
            """
            UPDATE session_results
            SET needs_human_review = 1, raw_json = ?
            WHERE id = ?
            """,
            (json.dumps(raw_json, ensure_ascii=False), result_id),
        )

    def _insert_detail(
        self,
        result_id: int,
        detail: QuestionGradingDetail,
    ) -> None:
        self.session.connection.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_id, knowledge_ids, error_category, error_summary,
                confidence_score, secondary_errors_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _serialize_secondary_errors(
                    getattr(detail, "secondary_errors", [])
                ),
            ),
        )

    def get_student_result_for_retry(
        self,
        session_id: int,
        student_id: int,
    ) -> dict[str, Any] | None:
        row = self.session.connection.execute(
            """
            SELECT
                id AS result_id,
                total_score,
                student_score,
                needs_human_review,
                raw_json
            FROM session_results
            WHERE session_id = ? AND student_id = ?
            """,
            (int(session_id), int(student_id)),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["needs_human_review"] = bool(item["needs_human_review"])
        loaded = _safe_json_loads(item.get("raw_json"))
        item["raw_json"] = loaded if isinstance(loaded, dict) else {}
        item["details"] = self.get_result_details(int(item["result_id"]))
        return item


class ResultRepositoryGateway:
    """Open one repository session per result operation."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

    def save_session_result(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
        grading_result: GradingResult,
    ) -> int:
        with self._sessions.session() as session:
            with session.transaction():
                return ResultRepository(session).save_session_result(
                    session_id,
                    student_id,
                    paper_id,
                    grading_result,
                )

    def publish_session_result_if_current_assignment(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
        grading_result: GradingResult,
    ) -> int | None:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                repository = ResultRepository(session)
                if not repository.current_assignment_matches(
                    session_id,
                    student_id,
                    paper_id,
                ):
                    return None
                result_id = repository.save_session_result(
                    session_id,
                    student_id,
                    paper_id,
                    grading_result,
                )
                PaperRepository(session).update_exam_paper_status(
                    paper_id,
                    "graded",
                    None,
                )
                return result_id

    def get_session_results(self, session_id: int) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_session_results(session_id)

    def get_result_details(self, result_id: int) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_result_details(result_id)

    def get_result_context(
        self,
        result_id: int,
    ) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_result_context(result_id)

    def get_session_completeness_source(
        self,
        session_id: int,
    ) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(
                session
            ).get_session_completeness_source(session_id)

    def get_session_weak_point_rows(
        self,
        session_id: int,
        student_id: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_session_weak_point_rows(
                session_id,
                student_id,
            )

    def get_active_weak_point_rows(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_active_weak_point_rows(
                student_id,
                session_ids,
            )

    def get_active_assessment_rows(
        self,
        *,
        student_ids: list[int],
        session_ids: list[int],
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_active_assessment_rows(
                student_ids=student_ids,
                session_ids=session_ids,
            )

    def get_active_error_point_rows(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_active_error_point_rows(
                student_id,
                session_ids,
            )

    def get_active_student_score_rates(self) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_active_student_score_rates()

    def get_active_detail_rows(
        self,
        *,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_active_detail_rows(
                student_id=student_id,
                session_ids=session_ids,
            )

    def replace_result_details_atomic(
        self,
        result_id: int,
        remove_question_ids: list[str],
        replacement_details: list[QuestionGradingDetail],
        *,
        student_score: float,
        needs_human_review: bool,
        raw_json: dict[str, Any],
        rubric: dict[str, Any] | None = None,
    ) -> None:
        del student_score
        with self._sessions.session() as session:
            with session.transaction():
                ResultRepository(session).replace_result_details_atomic(
                    result_id,
                    remove_question_ids,
                    replacement_details,
                    needs_human_review=needs_human_review,
                    raw_json=raw_json,
                    rubric=rubric,
                )

    def record_result_retry_failure(
        self,
        result_id: int,
        attempt: dict[str, Any],
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                ResultRepository(session).record_result_retry_failure(
                    result_id,
                    attempt,
                )

    def get_student_result_for_retry(
        self,
        session_id: int,
        student_id: int,
    ) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return ResultRepository(session).get_student_result_for_retry(
                session_id,
                student_id,
            )


def _safe_json_loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _detail_knowledge_ids(detail: Any) -> list[str]:
    raw_values = getattr(detail, "knowledge_ids", None)
    values: list[Any] = []
    if isinstance(raw_values, list):
        values.extend(raw_values)
    elif isinstance(raw_values, tuple):
        values.extend(raw_values)
    elif isinstance(raw_values, str) and raw_values.strip():
        parsed = _safe_json_loads(raw_values)
        if isinstance(parsed, list):
            values.extend(parsed)
        else:
            values.extend(raw_values.replace("|", ",").split(","))
    fallback = getattr(detail, "knowledge_id", None)
    if fallback:
        values.append(fallback)
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized or ["UNKNOWN"]


def _serialize_secondary_errors(errors: Any) -> str:
    payload: list[dict[str, str]] = []
    for raw_error in errors if isinstance(errors, (list, tuple)) else []:
        if isinstance(raw_error, dict):
            category = str(raw_error.get("category") or "").strip()
            summary = str(raw_error.get("summary") or "").strip()
            evidence = str(raw_error.get("evidence") or "").strip()
        else:
            category = str(getattr(raw_error, "category", "") or "").strip()
            summary = str(getattr(raw_error, "summary", "") or "").strip()
            evidence = str(getattr(raw_error, "evidence", "") or "").strip()
        if not category or not summary:
            continue
        payload.append(
            {"category": category, "summary": summary, "evidence": evidence}
        )
        if len(payload) == 2:
            break
    return json.dumps(payload, ensure_ascii=False)


def _parse_secondary_errors(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        summary = str(item.get("summary") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if not category or not summary:
            continue
        result.append(
            {"category": category, "summary": summary, "evidence": evidence}
        )
        if len(result) == 2:
            break
    return result


def _detail_row_with_secondary_errors(
    row: dict[str, Any],
) -> dict[str, Any]:
    row["secondary_errors"] = _parse_secondary_errors(
        row.get("secondary_errors_json")
    )
    return row
