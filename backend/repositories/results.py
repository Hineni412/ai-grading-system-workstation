"""Grading-result and detail persistence boundaries."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from backend.domain_models import (
    GradingResult,
    QuestionGradingDetail,
    detail_ai_score,
)
from backend.repositories.base import RepositorySession, RepositorySessionProvider
from backend.repositories.papers import (
    PaperRepository,
    sanitize_incomplete_failure_summary,
)
from grading_completeness import (
    audit_grading_details,
    merge_detail_metadata,
    details_require_review,
    resolve_grading_completeness,
)
from path_manager import resolve_stored_file_path
from solution_answer_guard import integer_business_score

try:
    from question_bank.taxonomy.registry import (
        canonicalize_knowledge as _registry_canonicalize,
    )
except Exception:
    _registry_canonicalize = None  # type: ignore[assignment]


def _validate_new_ai_details(
    details: list[QuestionGradingDetail], raw_json: dict[str, Any],
) -> None:
    """Check incoming grades before either full-save or selected retry writes."""
    question_ids = {str(detail.question_id) for detail in details}
    for detail in details:
        if integer_business_score(detail.score_awarded) is None:
            raise ValueError("score_contract_error: 新 AI 成绩必须为非负整数")
    metadata = raw_json.get("detail_metadata", {})
    if isinstance(metadata, dict) and any(
        str(question_id) in question_ids and isinstance(item, dict)
        and (item.get("step_assessments_error") or item.get("score_contract_error"))
        for question_id, item in metadata.items()
    ):
        raise ValueError("score_contract_error: 评分依据校验未通过，未保存成绩")


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
        *,
        scan_batch_id: str | None = None,
    ) -> int:
        # 在覆盖既有成绩、合并教师锁之前拒绝无效的新 AI 结果。
        # 历史读取和锁定的教师最终分不经过这条新成绩校验。
        _validate_new_ai_details(grading_result.grading_details, grading_result.raw_json or {})
        details, student_score, raw_json = self._merge_teacher_locks(
            session_id=session_id,
            student_id=student_id,
            scan_batch_id=scan_batch_id,
            grading_result=grading_result,
        )
        ai_scores = [detail_ai_score(detail) for detail in details]
        known_ai_scores = [score for score in ai_scores if score is not None]
        ai_student_score = (
            float(sum(known_ai_scores)) if known_ai_scores else None
        )
        old_rows = self.session.connection.execute(
            """
            SELECT id, raw_json
            FROM session_results
            WHERE session_id = ? AND student_id = ?
            """,
            (session_id, student_id),
        ).fetchall()
        raw_json.pop("teacher_reviews", None)
        for old_row in old_rows:
            old_payload = _safe_json_loads(old_row["raw_json"])
            reviews = old_payload.get("teacher_reviews", {}) if isinstance(old_payload, dict) else {}
            if not isinstance(reviews, dict):
                continue
            for qid, review in reviews.items():
                if isinstance(review, dict) and review.get("scan_batch_id") == scan_batch_id:
                    raw_json.setdefault("teacher_reviews", {})[qid] = review
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
                needs_human_review, raw_json, ai_student_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                student_id,
                paper_id,
                grading_result.total_score,
                student_score,
                1 if grading_result.needs_human_review else 0,
                json.dumps(raw_json, ensure_ascii=False),
                ai_student_score,
            ),
        )
        result_id = int(cursor.lastrowid)
        for detail, ai_score in zip(details, ai_scores):
            self.session.connection.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary,
                    confidence_score, secondary_errors_json, ai_score_awarded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    detail.question_id,
                    detail.score_awarded,
                    detail.deduction_reason,
                    json.dumps(_detail_knowledge_ids(detail), ensure_ascii=False),
                    getattr(detail, "error_category", None),
                    getattr(detail, "error_summary", None),
                    getattr(detail, "confidence_score", None),
                    _serialize_secondary_errors(
                        getattr(detail, "secondary_errors", [])
                    ),
                    ai_score,
                ),
            )
        return result_id

    def _merge_teacher_locks(
        self,
        *,
        session_id: int,
        student_id: int,
        scan_batch_id: str | None,
        grading_result: GradingResult,
    ) -> tuple[list[QuestionGradingDetail], float, dict[str, Any]]:
        details_by_qid = {
            str(detail.question_id): detail
            for detail in grading_result.grading_details
        }
        if not scan_batch_id:
            return (
                list(details_by_qid.values()),
                float(grading_result.student_score),
                dict(grading_result.raw_json or {}),
            )

        rows = self.session.connection.execute(
            """
            SELECT
                question_id, score_awarded, deduction_reason, revision
            FROM teacher_score_locks
            WHERE session_id = ? AND scan_batch_id = ? AND student_id = ?
            ORDER BY id
            """,
            (int(session_id), str(scan_batch_id), int(student_id)),
        ).fetchall()
        if not rows:
            return (
                list(details_by_qid.values()),
                float(grading_result.student_score),
                dict(grading_result.raw_json or {}),
            )

        locked_question_ids: list[str] = []
        for row in rows:
            question_id = str(row["question_id"])
            previous = details_by_qid.get(question_id)
            details_by_qid[question_id] = QuestionGradingDetail(
                question_id=question_id,
                score_awarded=float(row["score_awarded"]),
                deduction_reason=(
                    str(row["deduction_reason"])
                    if row["deduction_reason"] is not None
                    else "教师人工批改已确认"
                ),
                knowledge_id=(
                    previous.knowledge_id if previous is not None else "UNKNOWN"
                ),
                error_category="教师已确认",
                error_summary="teacher_score_locked",
                confidence_score=(
                    previous.confidence_score if previous is not None else None
                ),
                knowledge_ids=(
                    list(previous.knowledge_ids) if previous is not None else []
                ),
                secondary_errors=(
                    list(previous.secondary_errors) if previous is not None else []
                ),
                ai_score_awarded=(
                    detail_ai_score(previous) if previous is not None else None
                ),
            )
            locked_question_ids.append(question_id)

        details = list(details_by_qid.values())
        raw_json = dict(grading_result.raw_json or {})
        raw_json["teacher_score_locks"] = {
            "scan_batch_id": str(scan_batch_id),
            "question_ids": locked_question_ids,
        }
        return details, sum(float(item.score_awarded) for item in details), raw_json

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
                sr.ai_student_score,
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
                knowledge_ids,
                error_category,
                error_summary,
                confidence_score,
                secondary_errors_json,
                ai_score_awarded
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
            ).append(_detail_row_with_knowledge_ids(dict(row)))
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
        query += (
            " ORDER BY s.id ASC, "
            "json_extract(sd.knowledge_ids, '$[0]') ASC, sd.id ASC"
        )
        rows = self.session.connection.execute(query, params).fetchall()
        return [_detail_row_with_knowledge_ids(dict(row)) for row in rows]

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
        query += (
            " ORDER BY s.id ASC, "
            "json_extract(sd.knowledge_ids, '$[0]') ASC, sd.id ASC"
        )
        rows = self.session.connection.execute(query, params).fetchall()
        return [_detail_row_with_knowledge_ids(dict(row)) for row in rows]

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
                gs.created_at AS exam_created_at,
                sr.id AS result_id,
                sr.student_id,
                sr.student_score,
                sr.total_score,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                ep.front_image,
                ep.back_image,
                sd.id AS detail_id,
                sd.question_id,
                sd.knowledge_ids,
                COALESCE(tsl.score_awarded, sd.score_awarded) AS score_awarded,
                tsl.max_score AS teacher_final_max_score,
                tsl.revision AS teacher_final_revision,
                tsl.scan_batch_id AS teacher_final_scan_batch_id,
                sr.raw_json AS assessment_raw_json,
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
            LEFT JOIN teacher_score_locks tsl
                ON tsl.session_id = sr.session_id AND tsl.student_id = sr.student_id
                AND tsl.question_id = sd.question_id
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
        result = []
        metadata_by_result = {}
        teacher_reviews_by_result = {}
        for raw_row in rows:
            row = dict(raw_row)
            raw = row.pop("assessment_raw_json", None)
            if row["result_id"] not in metadata_by_result:
                try:
                    payload = json.loads(raw or "{}")
                    metadata = payload.get("detail_metadata", {})
                    metadata_by_result[row["result_id"]] = metadata if isinstance(metadata, dict) else {}
                    teacher_reviews = payload.get("teacher_reviews")
                    teacher_reviews_by_result[row["result_id"]] = teacher_reviews if isinstance(teacher_reviews, dict) else {}
                except (TypeError, ValueError, AttributeError):
                    metadata_by_result[row["result_id"]] = {}
            detail = metadata_by_result[row["result_id"]].get(row["question_id"], {})
            row["assessment_state"] = {
                key: detail.get(key) for key in (
                    "need_review", "answer_is_blank_or_no_valid_work", "answer_discarded_by_smudge",
                    "step_assessments",
                ) if key in detail
            } if isinstance(detail, dict) else {}
            teacher_reviews = teacher_reviews_by_result.get(row["result_id"], {})
            teacher_review = teacher_reviews.get(row["question_id"])
            if isinstance(teacher_review, dict):
                row["assessment_state"]["teacher_review"] = teacher_review
            result.append(_detail_row_with_secondary_errors(row))
        return result

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
        return [_detail_row_with_knowledge_ids(dict(row)) for row in rows]

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
        scan_batch_id: str | None = None,
    ) -> None:
        _validate_new_ai_details(replacement_details, raw_json)
        owner = self.session.connection.execute(
            "SELECT session_id, student_id, raw_json FROM session_results WHERE id = ?",
            (result_id,),
        ).fetchone()
        if owner is None:
            raise ValueError(f"Unknown session result: {result_id}")

        locked_rows: list[Any] = []
        locked_question_ids: set[str] = set()
        if scan_batch_id:
            locked_rows = self.session.connection.execute(
                """
                SELECT
                    question_id, score_awarded, deduction_reason, revision
                FROM teacher_score_locks
                WHERE session_id = ?
                  AND scan_batch_id = ?
                  AND student_id = ?
                ORDER BY id
                """,
                (
                    int(owner["session_id"]),
                    str(scan_batch_id),
                    int(owner["student_id"]),
                ),
            ).fetchall()
            locked_question_ids = {
                str(row["question_id"]) for row in locked_rows
            }
        question_ids = list(
            dict.fromkeys(
                str(value)
                for value in remove_question_ids
                if str(value) not in locked_question_ids
            )
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
        locked_replacement_ai_scores = {
            str(detail.question_id): detail_ai_score(detail)
            for detail in replacement_details
            if str(detail.question_id) in locked_question_ids
        }
        for detail in replacement_details:
            if str(detail.question_id) in locked_question_ids:
                continue
            self._insert_detail(result_id, detail)

        # A teacher can confirm an item that was missing from an incomplete AI
        # result.  Preserve/update existing locked rows and materialize a
        # missing detail before the completeness audit, so an explicit retry
        # cannot fail merely because it correctly skipped the teacher's item.
        # Locked rows keep the teacher score as final; a fresh AI score from
        # this run is still recorded in ai_score_awarded.
        for lock in locked_rows:
            question_id = str(lock["question_id"])
            existing = self.session.connection.execute(
                """
                SELECT id
                FROM session_details
                WHERE result_id = ? AND question_id = ?
                ORDER BY id
                """,
                (result_id, question_id),
            ).fetchall()
            if existing:
                ai_score = locked_replacement_ai_scores.get(question_id)
                for row in existing:
                    if ai_score is not None:
                        self.session.connection.execute(
                            """
                            UPDATE session_details
                            SET score_awarded = ?,
                                deduction_reason = ?,
                                error_category = '教师已确认',
                                error_summary = 'teacher_score_locked',
                                ai_score_awarded = ?
                            WHERE id = ?
                            """,
                            (
                                float(lock["score_awarded"]),
                                lock["deduction_reason"]
                                or "教师人工批改已确认",
                                ai_score,
                                int(row["id"]),
                            ),
                        )
                        continue
                    self.session.connection.execute(
                        """
                        UPDATE session_details
                        SET score_awarded = ?,
                            deduction_reason = ?,
                            error_category = '教师已确认',
                            error_summary = 'teacher_score_locked'
                        WHERE id = ?
                        """,
                        (
                            float(lock["score_awarded"]),
                            lock["deduction_reason"]
                            or "教师人工批改已确认",
                            int(row["id"]),
                        ),
                    )
                continue
            self._insert_detail(
                result_id,
                QuestionGradingDetail(
                    question_id=question_id,
                    score_awarded=float(lock["score_awarded"]),
                    deduction_reason=(
                        str(lock["deduction_reason"])
                        if lock["deduction_reason"] is not None
                        else "教师人工批改已确认"
                    ),
                    error_category="教师已确认",
                    error_summary="teacher_score_locked",
                ),
            )

        stored_details = [
            _detail_row_with_knowledge_ids(dict(row))
            for row in self.session.connection.execute(
                """
                SELECT
                    question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary,
                    confidence_score, secondary_errors_json, ai_score_awarded
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
        known_ai_scores = [
            float(detail["ai_score_awarded"])
            for detail in stored_details
            if detail["ai_score_awarded"] is not None
        ]
        recalculated_ai_score = (
            float(sum(known_ai_scores)) if known_ai_scores else None
        )
        persisted_raw_json = merge_detail_metadata(owner["raw_json"], raw_json,
            [*question_ids, *(str(d.question_id) for d in replacement_details)])
        persisted_raw_json.pop("teacher_reviews", None)
        old_payload = _safe_json_loads(owner["raw_json"])
        if isinstance(old_payload, dict) and isinstance(old_payload.get("teacher_reviews"), dict):
            persisted_raw_json["teacher_reviews"] = old_payload["teacher_reviews"]
        if scan_batch_id and locked_rows:
            persisted_raw_json["teacher_score_locks"] = {
                "scan_batch_id": str(scan_batch_id),
                "question_ids": [
                    str(row["question_id"]) for row in locked_rows
                ],
            }
        if rubric is not None:
            completeness = audit_grading_details(rubric, stored_details)
            if completeness["status"] != "complete":
                raise ValueError(
                    "Atomic replacement must leave a complete grading result; "
                    f"got {completeness['status']}"
                )
            persisted_raw_json["grading_completeness"] = completeness
            needs_human_review = details_require_review(stored_details, persisted_raw_json)
        self.session.connection.execute(
            """
            UPDATE session_results
            SET student_score = ?, needs_human_review = ?, raw_json = ?,
                ai_student_score = ?
            WHERE id = ?
            """,
            (
                float(recalculated_score),
                1 if needs_human_review else 0,
                json.dumps(persisted_raw_json, ensure_ascii=False),
                recalculated_ai_score,
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
                knowledge_ids, error_category, error_summary,
                confidence_score, secondary_errors_json, ai_score_awarded
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                detail.question_id,
                detail.score_awarded,
                detail.deduction_reason,
                json.dumps(_detail_knowledge_ids(detail), ensure_ascii=False),
                getattr(detail, "error_category", None),
                getattr(detail, "error_summary", None),
                getattr(detail, "confidence_score", None),
                _serialize_secondary_errors(
                    getattr(detail, "secondary_errors", [])
                ),
                detail_ai_score(detail),
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

    def __init__(
        self,
        sessions: RepositorySessionProvider,
        *,
        db_path: Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._db_path = (
            Path(db_path)
            if db_path is not None
            else getattr(sessions, "database", None)
        )
        self._rubric_map_cache: dict[int, dict[str, dict[str, Any]]] = {}

    def _invalidate_rubric_maps(self, session_id: int) -> None:
        self._rubric_map_cache.pop(session_id, None)

    def save_session_result(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
        grading_result: GradingResult,
        *,
        scan_batch_id: str | None = None,
    ) -> int:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                return ResultRepository(session).save_session_result(
                    session_id,
                    student_id,
                    paper_id,
                    grading_result,
                    scan_batch_id=scan_batch_id,
                )

    def publish_session_result_if_current_assignment(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
        grading_result: GradingResult,
        *,
        scan_batch_id: str | None = None,
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
                    scan_batch_id=scan_batch_id,
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
        scan_batch_id: str | None = None,
    ) -> None:
        del student_score
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                ResultRepository(session).replace_result_details_atomic(
                    result_id,
                    remove_question_ids,
                    replacement_details,
                    needs_human_review=needs_human_review,
                    raw_json=raw_json,
                    rubric=rubric,
                    scan_batch_id=scan_batch_id,
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

    def list_incomplete_results(self, session_id: int) -> list[dict[str, Any]]:
        rubric = self._load_session_rubric(session_id)
        result_rows, details_by_result = self.get_session_completeness_source(
            session_id
        )
        if not result_rows:
            return []

        items: list[dict[str, Any]] = []
        for row in result_rows:
            parsed_raw_json = _safe_json_loads(row["raw_json"])
            completeness = resolve_grading_completeness(
                parsed_raw_json,
                rubric=rubric,
                details=details_by_result.get(int(row["result_id"]), []),
            )
            if not isinstance(completeness, dict):
                continue
            if completeness.get("status") not in {"incomplete", "invalid"}:
                continue
            retry_attempts = _grading_retry_attempts(parsed_raw_json)
            items.append(
                {
                    "result_id": int(row["result_id"]),
                    "student_id": int(row["student_id"]),
                    "paper_id": int(row["paper_id"]),
                    "student_code": row["student_code"],
                    "student_name": row["student_name"],
                    "class_name": row["class_name"],
                    "ocr_name": row["ocr_name"],
                    "front_image": row["front_image"],
                    "back_image": row["back_image"],
                    "match_status": row["match_status"],
                    "processing_status": row["processing_status"],
                    "status": completeness["status"],
                    "missing_question_ids": list(completeness.get("missing_question_ids", [])),
                    "affected_major_question_ids": list(completeness.get("affected_major_question_ids", [])),
                    "last_failure_reason": _last_incomplete_failure_reason(
                        parsed_raw_json,
                        fallback_error=row["error_message"],
                        completeness_status=str(completeness.get("status") or ""),
                    ),
                    "retry_attempt_count": len(retry_attempts),
                }
            )
        return items

    def get_session_weak_points(self, session_id: int, student_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.get_session_weak_point_rows(
            session_id,
            student_id,
        )
        return self._build_weak_point_rows(rows)

    def get_active_global_weak_points(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.get_active_weak_point_rows(
            student_id,
            session_ids,
        )
        return self._build_weak_point_rows(rows)

    def get_active_assessment_evidence(
        self,
        *,
        student_ids: list[str] | tuple[str, ...] = (),
        session_ids: list[int] | tuple[int, ...] = (),
    ) -> list[dict[str, Any]]:
        normalized_students = [int(value) for value in student_ids]
        normalized_sessions = [int(value) for value in session_ids]
        rows = self.get_active_assessment_rows(
            student_ids=normalized_students,
            session_ids=normalized_sessions,
        )
        enriched = self._enrich_detail_rows(rows)
        for row in enriched:
            row["full_score"] = _safe_float(row.get("max_score"), 0.0)
        return enriched

    def get_active_global_error_points(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.get_active_error_point_rows(
            student_id,
            session_ids,
        )
        return self._build_error_point_rows(rows)

    def get_active_wrong_items_for_knowledge(self, student_id: int, knowledge_id: str) -> list[dict[str, Any]]:
        return [
            item for item in self.get_active_items_for_knowledge(student_id, knowledge_id)
            if item.get("is_deducted")
        ]

    def get_active_items_for_knowledge(self, student_id: int, knowledge_id: str) -> list[dict[str, Any]]:
        rows = self.get_active_detail_rows(
            student_id=student_id
        )

        result: list[dict[str, Any]] = []
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}
        for row in rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            qid = str(row.get("question_id") or "")
            row_knowledge_ids = _knowledge_ids_from_result_row(row)
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
        return self.get_active_detail_rows(
            student_id=student_id,
            session_ids=session_ids,
        )

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
            row_knowledge_ids = _knowledge_ids_from_result_row(row)
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
            knowledge_ids = _knowledge_ids_from_result_row(row)
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
        if session_id in self._rubric_map_cache:
            return self._rubric_map_cache[session_id]

        session = self._get_grading_session_row(session_id)
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

    def _load_session_rubric(self, session_id: int) -> dict[str, Any]:
        session = self._get_grading_session_row(session_id)
        if not session:
            return {}
        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            return {}
        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return rubric if isinstance(rubric, dict) else {}

    def _get_grading_session_row(self, session_id: int) -> dict[str, Any] | None:
        from backend.repositories.sessions import SessionRepository

        with self._sessions.session(read_only=True) as session:
            return SessionRepository(session).get_grading_session(session_id)

    def _resolve_stored_file_path(self, path_value: object) -> Path:
        db_path = self._db_path
        data_root = (
            db_path.parent.parent
            if db_path is not None and db_path.parent.name == "databases"
            else None
        )
        return resolve_stored_file_path(path_value, data_root=data_root)


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
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    if normalized:
        return normalized
    fallback = str(getattr(detail, "knowledge_id", None) or "").strip()
    return [fallback] if fallback else ["UNKNOWN"]


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
    _detail_row_with_knowledge_ids(row)
    row["secondary_errors"] = _parse_secondary_errors(
        row.get("secondary_errors_json")
    )
    return row


def _detail_row_with_knowledge_ids(
    row: dict[str, Any],
) -> dict[str, Any]:
    knowledge_ids = _knowledge_ids_from_row(row)
    row["knowledge_ids"] = knowledge_ids
    row["knowledge_id"] = knowledge_ids[0]
    return row


def _knowledge_ids_from_row(row: dict[str, Any]) -> list[str]:
    raw_values = row.get("knowledge_ids")
    if isinstance(raw_values, str):
        parsed = _safe_json_loads(raw_values)
        raw_values = parsed if isinstance(parsed, list) else []
    normalized: list[str] = []
    for value in raw_values if isinstance(raw_values, (list, tuple)) else []:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    if normalized:
        return normalized
    fallback = str(row.get("knowledge_id") or "").strip()
    return [fallback] if fallback else ["UNKNOWN"]


def _grading_retry_attempts(raw_json: Any) -> list[dict[str, Any]]:
    parsed = _safe_json_loads(raw_json)
    attempts = parsed.get("grading_retry_attempts") if isinstance(parsed, dict) else None
    if not isinstance(attempts, list):
        return []
    return [dict(item) for item in attempts if isinstance(item, dict)]


def _last_incomplete_failure_reason(raw_json: Any, *, fallback_error: Any, completeness_status: str) -> str:
    raw_reason = ""
    attempts = _grading_retry_attempts(raw_json)
    if attempts:
        latest = attempts[-1]
        for key in ("error", "message", "reason"):
            value = str(latest.get(key) or "").strip()
            if value:
                raw_reason = value
                break

    parsed = _safe_json_loads(raw_json)
    if not raw_reason and isinstance(parsed, dict):
        legacy = parsed.get("hybrid_batch_fallback")
        if isinstance(legacy, dict):
            items = legacy.get("items") if isinstance(legacy.get("items"), list) else []
            reasons = _unique_text_list(
                [
                    item.get("reason")
                    for item in items
                    if isinstance(item, dict)
                ]
            )
            if reasons:
                raw_reason = "；".join(reasons)

    if not raw_reason:
        raw_reason = str(fallback_error or "").strip()
    safe_reason = sanitize_incomplete_failure_summary(raw_reason)
    if safe_reason:
        return safe_reason
    if completeness_status == "invalid":
        return "批改结果存在异常题目或分值，建议补跑受影响大题"
    return "批改结果缺少部分小题，建议补跑受影响大题"


def _unique_text_list(values: Any) -> list[str]:
    if isinstance(values, (str, bytes)):
        values = [values]
    elif not isinstance(values, list):
        try:
            values = list(values)
        except TypeError:
            values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _knowledge_ids_from_result_row(row: dict[str, Any]) -> list[str]:
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
    return any("一" <= ch <= "鿿" for ch in str(text or ""))


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
