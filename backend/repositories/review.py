"""Teacher-review persistence and ownership checks."""

from __future__ import annotations

from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider
from backend.repositories.results import _safe_json_loads


class ReviewAdjustmentOwnershipError(ValueError):
    def __init__(
        self,
        *,
        session_id: int,
        result_id: int,
        question_id: str,
        detail_id: int,
    ) -> None:
        self.session_id = int(session_id)
        self.result_id = int(result_id)
        self.question_id = str(question_id)
        self.detail_id = int(detail_id)
        super().__init__(
            f"Review detail {self.detail_id} does not belong to the requested "
            "session, result, and question."
        )


class ReviewRepository:
    """Session-bound review writes without transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def apply_session_review_adjustments(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        if not adjustments:
            return {"updated_details": 0, "updated_results": 0}

        requested_session_id = int(session_id)
        validated: list[tuple[dict[str, Any], int]] = []
        seen_detail_ids: set[int] = set()
        for item in adjustments:
            detail_id = int(item["detail_id"])
            if detail_id in seen_detail_ids:
                raise ValueError(f"Duplicate review detail_id: {detail_id}")
            seen_detail_ids.add(detail_id)

            expected_session_id = int(item["session_id"])
            expected_result_id = int(item["result_id"])
            expected_question_id = str(item["question_id"] or "").strip()
            row = self.session.connection.execute(
                """
                SELECT
                    sd.id AS detail_id,
                    sd.result_id,
                    sd.question_id,
                    sr.session_id
                FROM session_details sd
                JOIN session_results sr ON sr.id = sd.result_id
                WHERE sd.id = ?
                """,
                (detail_id,),
            ).fetchone()
            if (
                expected_session_id != requested_session_id
                or row is None
                or int(row["session_id"]) != requested_session_id
                or int(row["result_id"]) != expected_result_id
                or str(row["question_id"] or "").strip() != expected_question_id
            ):
                raise ReviewAdjustmentOwnershipError(
                    session_id=requested_session_id,
                    result_id=expected_result_id,
                    question_id=expected_question_id,
                    detail_id=detail_id,
                )
            validated.append((item, expected_result_id))

        for item, _result_id in validated:
            cursor = self.session.connection.execute(
                """
                UPDATE session_details
                SET score_awarded = ?, deduction_reason = ?,
                    error_category = ?, error_summary = ?
                WHERE id = ?
                """,
                (
                    float(item["score_awarded"]),
                    item.get("deduction_reason"),
                    item.get("error_category"),
                    item.get("error_summary"),
                    int(item["detail_id"]),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Review detail update affected {cursor.rowcount} rows."
                )

        result_ids = sorted({result_id for _item, result_id in validated})
        for result_id in result_ids:
            total = self.session.connection.execute(
                """
                SELECT COALESCE(SUM(score_awarded), 0) AS total
                FROM session_details
                WHERE result_id = ?
                """,
                (result_id,),
            ).fetchone()["total"]
            cursor = self.session.connection.execute(
                """
                UPDATE session_results
                SET student_score = ?
                WHERE id = ? AND session_id = ?
                """,
                (float(total), result_id, requested_session_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Review result update affected {cursor.rowcount} rows."
                )

        return {
            "updated_details": len(validated),
            "updated_results": len(result_ids),
        }

    def get_session_review_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT
                sr.id AS result_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                ep.ocr_name,
                sr.raw_json,
                sd.id AS detail_id,
                sd.question_id,
                sd.score_awarded,
                sd.deduction_reason,
                sd.error_category,
                sd.error_summary,
                sd.confidence_score
            FROM session_results sr
            JOIN students s ON s.id = sr.student_id
            JOIN exam_papers ep ON ep.id = sr.paper_id
            JOIN session_details sd ON sd.result_id = sr.id
            WHERE sr.session_id = ?
            ORDER BY sr.id, sd.id
            """,
            (session_id,),
        ).fetchall()
        parsed_raw_json: dict[int, Any] = {}
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            result_id = int(item.get("result_id") or 0)
            if result_id not in parsed_raw_json:
                parsed_raw_json[result_id] = _safe_json_loads(
                    item.get("raw_json")
                )
            item["raw_json"] = parsed_raw_json[result_id]
            result.append(item)
        return result

    def get_review_media_context(
        self,
        session_id: int,
        result_id: int,
        detail_id: int,
    ) -> dict[str, Any] | None:
        row = self.session.connection.execute(
            """
            SELECT
                sr.session_id,
                sr.id AS result_id,
                sd.id AS detail_id,
                sd.question_id,
                ep.front_image,
                ep.back_image
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN exam_papers ep ON ep.id = sr.paper_id
            WHERE sr.session_id = ?
              AND sr.id = ?
              AND sd.id = ?
            """,
            (int(session_id), int(result_id), int(detail_id)),
        ).fetchone()
        return dict(row) if row else None

    def update_result_detail(
        self,
        detail_id: int,
        score_awarded: float,
        deduction_reason: str | None,
        error_category: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        self.session.connection.execute(
            """
            UPDATE session_details
            SET score_awarded = ?, deduction_reason = ?,
                error_category = ?, error_summary = ?
            WHERE id = ?
            """,
            (
                float(score_awarded),
                deduction_reason,
                error_category,
                error_summary,
                detail_id,
            ),
        )

    def recalculate_result_score(self, result_id: int) -> None:
        total = self.session.connection.execute(
            """
            SELECT COALESCE(SUM(score_awarded), 0) AS score
            FROM session_details
            WHERE result_id = ?
            """,
            (result_id,),
        ).fetchone()["score"]
        self.session.connection.execute(
            """
            UPDATE session_results
            SET student_score = ?
            WHERE id = ?
            """,
            (float(total), result_id),
        )

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
        placeholders = ",".join("?" for _ in detail_ids)
        rows = self.session.connection.execute(
            f"""
            SELECT sd.id AS detail_id, sd.result_id
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            WHERE sr.session_id = ? AND sd.id IN ({placeholders})
            """,
            [int(session_id), *detail_ids],
        ).fetchall()
        result_by_detail = {
            int(row["detail_id"]): int(row["result_id"]) for row in rows
        }
        missing_ids = [
            detail_id
            for detail_id in detail_ids
            if detail_id not in result_by_detail
        ]
        if missing_ids:
            raise ValueError(f"评分明细不属于当前考试：{missing_ids}")

        for item in adjustments:
            self.session.connection.execute(
                """
                UPDATE session_details
                SET score_awarded = ?
                WHERE id = ?
                """,
                (float(item["score_awarded"]), int(item["detail_id"])),
            )
        result_ids = sorted(set(result_by_detail.values()))
        for result_id in result_ids:
            self.recalculate_result_score(result_id)
        return {
            "updated_details": len(adjustments),
            "updated_results": len(result_ids),
        }

    def upsert_annotated_result(
        self,
        session_id: int,
        result_id: int,
        annotated_front_path: str,
        annotated_back_path: str,
    ) -> dict[str, Any] | None:
        requested_session_id = int(session_id)
        requested_result_id = int(result_id)
        owner = self.session.connection.execute(
            "SELECT session_id FROM session_results WHERE id = ?",
            (requested_result_id,),
        ).fetchone()
        if owner is None or int(owner["session_id"]) != requested_session_id:
            raise ValueError(
                "Annotated result must belong to the requested session."
            )
        existing = self.session.connection.execute(
            """
            SELECT
                id, session_id, result_id,
                annotated_front_path, annotated_back_path
            FROM annotated_results
            WHERE result_id = ?
            """,
            (requested_result_id,),
        ).fetchone()
        if existing:
            self.session.connection.execute(
                """
                UPDATE annotated_results
                SET session_id = ?, annotated_front_path = ?,
                    annotated_back_path = ?,
                    updated_at = datetime('now','localtime')
                WHERE result_id = ?
                """,
                (
                    requested_session_id,
                    annotated_front_path,
                    annotated_back_path,
                    requested_result_id,
                ),
            )
        else:
            self.session.connection.execute(
                """
                INSERT INTO annotated_results (
                    session_id, result_id,
                    annotated_front_path, annotated_back_path
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    requested_session_id,
                    requested_result_id,
                    annotated_front_path,
                    annotated_back_path,
                ),
            )
        return dict(existing) if existing else None

    def is_annotated_result_path_referenced(
        self,
        path_value: str,
    ) -> bool:
        row = self.session.connection.execute(
            """
            SELECT 1
            FROM annotated_results
            WHERE annotated_front_path = ? OR annotated_back_path = ?
            LIMIT 1
            """,
            (str(path_value), str(path_value)),
        ).fetchone()
        return row is not None

    def get_annotated_result(
        self,
        result_id: int,
    ) -> dict[str, Any] | None:
        row = self.session.connection.execute(
            """
            SELECT
                id, session_id, result_id,
                annotated_front_path, annotated_back_path, updated_at
            FROM annotated_results
            WHERE result_id = ?
            """,
            (result_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_session_annotation_path_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        rows = self.session.connection.execute(
            """
            SELECT annotated_front_path, annotated_back_path
            FROM annotated_results
            WHERE session_id = ?
            """,
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_session_annotations(
        self,
        session_id: int,
        result_ids: list[int],
    ) -> int:
        deleted = 0
        normalized_ids = [int(value) for value in result_ids]
        if normalized_ids:
            placeholders = ",".join("?" for _ in normalized_ids)
            cursor = self.session.connection.execute(
                f"""
                DELETE FROM annotated_results
                WHERE result_id IN ({placeholders})
                """,
                normalized_ids,
            )
            deleted += max(0, int(cursor.rowcount))
        cursor = self.session.connection.execute(
            "DELETE FROM annotated_results WHERE session_id = ?",
            (int(session_id),),
        )
        return deleted + max(0, int(cursor.rowcount))


class ReviewRepositoryGateway:
    """Open one immediate transaction for each review write batch."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

    def apply_session_review_adjustments(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        if not adjustments:
            return {"updated_details": 0, "updated_results": 0}
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                return ReviewRepository(
                    session
                ).apply_session_review_adjustments(
                    session_id,
                    adjustments,
                )

    def get_session_review_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ReviewRepository(session).get_session_review_rows(
                session_id
            )

    def get_review_media_context(
        self,
        session_id: int,
        result_id: int,
        detail_id: int,
    ) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return ReviewRepository(session).get_review_media_context(
                session_id,
                result_id,
                detail_id,
            )

    def update_result_detail(
        self,
        detail_id: int,
        score_awarded: float,
        deduction_reason: str | None,
        error_category: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                ReviewRepository(session).update_result_detail(
                    detail_id,
                    score_awarded,
                    deduction_reason,
                    error_category,
                    error_summary,
                )

    def recalculate_result_score(self, result_id: int) -> None:
        with self._sessions.session() as session:
            with session.transaction():
                ReviewRepository(session).recalculate_result_score(result_id)

    def update_session_detail_scores(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        if not adjustments:
            return {"updated_details": 0, "updated_results": 0}
        with self._sessions.session() as session:
            with session.transaction():
                return ReviewRepository(session).update_session_detail_scores(
                    session_id,
                    adjustments,
                )

    def upsert_annotated_result(
        self,
        session_id: int,
        result_id: int,
        annotated_front_path: str,
        annotated_back_path: str,
    ) -> dict[str, Any] | None:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                return ReviewRepository(session).upsert_annotated_result(
                    session_id,
                    result_id,
                    annotated_front_path,
                    annotated_back_path,
                )

    def is_annotated_result_path_referenced(
        self,
        path_value: str,
    ) -> bool:
        with self._sessions.session(read_only=True) as session:
            return ReviewRepository(
                session
            ).is_annotated_result_path_referenced(path_value)

    def get_annotated_result(
        self,
        result_id: int,
    ) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return ReviewRepository(session).get_annotated_result(result_id)

    def get_session_annotation_path_rows(
        self,
        session_id: int,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ReviewRepository(
                session
            ).get_session_annotation_path_rows(session_id)
