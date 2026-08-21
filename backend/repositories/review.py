"""Teacher-review persistence and ownership checks."""

from __future__ import annotations

import math
from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider
from backend.repositories.results import _safe_json_loads
from question_id_contract import question_id_coordinates


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


class TeacherScoreLockRevisionConflict(ValueError):
    """A teacher score confirmation was based on a stale lock revision."""

    def __init__(
        self,
        *,
        session_id: int,
        scan_batch_id: str,
        student_id: int,
        question_id: str,
        expected_revision: int,
        current_revision: int | None,
    ) -> None:
        self.session_id = int(session_id)
        self.scan_batch_id = str(scan_batch_id)
        self.student_id = int(student_id)
        self.question_id = str(question_id)
        self.expected_revision = int(expected_revision)
        self.current_revision = (
            None if current_revision is None else int(current_revision)
        )
        super().__init__(
            "Teacher score lock revision does not match the stored value."
        )


class TeacherScoreLockOwnershipError(ValueError):
    """A teacher score lock references data outside its declared owner."""


def _nonblank_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be nonblank.")
    return text


def _optional_nonblank_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _integer_at_least(value: Any, field_name: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"{field_name} must be an integer.")
    normalized = int(numeric)
    if normalized < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")
    return normalized


def _positive_integer(value: Any, field_name: str) -> int:
    return _integer_at_least(value, field_name, 1)


def _optional_positive_integer(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _positive_integer(value, field_name)


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number.") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be a finite number.")
    return normalized


def _normalize_teacher_score_confirmation(
    raw: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Teacher score confirmation must be an object.")
    score_awarded = _finite_number(raw.get("score_awarded"), "score_awarded")
    max_score = _finite_number(raw.get("max_score"), "max_score")
    if max_score <= 0:
        raise ValueError("max_score must be greater than zero.")
    if score_awarded < 0 or score_awarded > max_score:
        raise ValueError("score_awarded must be between zero and max_score.")
    deduction_reason = raw.get("deduction_reason")
    if deduction_reason is not None:
        deduction_reason = str(deduction_reason).strip() or None
    result_id = _optional_positive_integer(raw.get("result_id"), "result_id")
    detail_id = _optional_positive_integer(raw.get("detail_id"), "detail_id")
    if (result_id is None) != (detail_id is None):
        raise ValueError(
            "result_id and detail_id must either both be provided or both be null."
        )
    return {
        "student_id": _positive_integer(raw.get("student_id"), "student_id"),
        "question_id": _nonblank_text(
            raw.get("question_id"),
            "question_id",
        ),
        "score_awarded": score_awarded,
        "max_score": max_score,
        "deduction_reason": deduction_reason,
        "source_target_type": _nonblank_text(
            raw.get("source_target_type"),
            "source_target_type",
        ).casefold(),
        "source_target_id": _positive_integer(
            raw.get("source_target_id"),
            "source_target_id",
        ),
        "expected_revision": _integer_at_least(
            raw.get("expected_revision"),
            "expected_revision",
            0,
        ),
        "result_id": result_id,
        "detail_id": detail_id,
        "detail_question_id": (
            _optional_nonblank_text(raw.get("detail_question_id"))
            or _nonblank_text(raw.get("question_id"), "question_id")
        ),
    }


def _validate_teacher_score_lock_revision(
    *,
    session_id: int,
    scan_batch_id: str,
    item: dict[str, Any],
    current: dict[str, Any] | None,
) -> None:
    expected_revision = int(item["expected_revision"])
    current_revision = None if current is None else int(current["revision"])
    if (
        expected_revision == 0
        and current is None
        or expected_revision > 0
        and current_revision == expected_revision
    ):
        return
    raise TeacherScoreLockRevisionConflict(
        session_id=session_id,
        scan_batch_id=scan_batch_id,
        student_id=item["student_id"],
        question_id=item["question_id"],
        expected_revision=expected_revision,
        current_revision=current_revision,
    )


def _region_matches_question(region_question: str, requested_question: str) -> bool:
    region_identity = question_id_coordinates(region_question)
    requested_identity = question_id_coordinates(requested_question)
    if region_identity is None or requested_identity is None:
        return (
            str(region_question or "").strip()
            == str(requested_question or "").strip()
        )
    if region_identity == requested_identity:
        return True
    return (
        region_identity[0] == requested_identity[0]
        and (
            region_identity[1] is None
            or requested_identity[1] is None
        )
    )


class ReviewRepository:
    """Session-bound review writes without transaction ownership."""

    def __init__(self, session: RepositorySession) -> None:
        self._session = session

    @property
    def session(self) -> RepositorySession:
        return self._session

    def list_teacher_score_locks(
        self,
        session_id: int,
        scan_batch_id: str | None = None,
        *,
        student_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["session_id = ?"]
        parameters: list[Any] = [int(session_id)]
        if scan_batch_id is not None:
            clauses.append("scan_batch_id = ?")
            parameters.append(_nonblank_text(scan_batch_id, "scan_batch_id"))
        if student_id is not None:
            clauses.append("student_id = ?")
            parameters.append(_positive_integer(student_id, "student_id"))
        rows = self.session.connection.execute(
            f"""
            SELECT
                id, session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id, revision,
                created_at, updated_at
            FROM teacher_score_locks
            WHERE {" AND ".join(clauses)}
            ORDER BY scan_batch_id, student_id, question_id, id
            """,
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_teacher_score_lock(
        self,
        session_id: int,
        scan_batch_id: str,
        student_id: int,
        question_id: str,
    ) -> dict[str, Any] | None:
        row = self.session.connection.execute(
            """
            SELECT
                id, session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id, revision,
                created_at, updated_at
            FROM teacher_score_locks
            WHERE session_id = ?
              AND scan_batch_id = ?
              AND student_id = ?
              AND question_id = ?
            """,
            (
                int(session_id),
                _nonblank_text(scan_batch_id, "scan_batch_id"),
                _positive_integer(student_id, "student_id"),
                _nonblank_text(question_id, "question_id"),
            ),
        ).fetchone()
        return dict(row) if row else None

    def confirm_teacher_score_lock(
        self,
        *,
        session_id: int,
        scan_batch_id: str,
        student_id: int,
        question_id: str,
        score_awarded: float,
        max_score: float,
        deduction_reason: str | None,
        source_target_type: str,
        source_target_id: int,
        expected_revision: int,
        sync_existing_detail: bool = True,
    ) -> dict[str, Any]:
        report = self.confirm_teacher_score_locks(
            session_id,
            scan_batch_id,
            [
                {
                    "student_id": student_id,
                    "question_id": question_id,
                    "score_awarded": score_awarded,
                    "max_score": max_score,
                    "deduction_reason": deduction_reason,
                    "source_target_type": source_target_type,
                    "source_target_id": source_target_id,
                    "expected_revision": expected_revision,
                }
            ],
            sync_existing_details=sync_existing_detail,
        )
        return {
            "lock": report["locks"][0],
            "inserted": report["inserted"] == 1,
            "synced_details": report["synced_details"],
            "updated_results": report["updated_results"],
        }

    def confirm_teacher_scores(
        self,
        session_id: int,
        scan_batch_id: str,
        confirmations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.confirm_teacher_score_locks(
            session_id,
            scan_batch_id,
            confirmations,
        )

    def confirm_teacher_score_locks(
        self,
        session_id: int,
        scan_batch_id: str,
        confirmations: list[dict[str, Any]],
        *,
        sync_existing_details: bool = True,
    ) -> dict[str, Any]:
        """Insert new confirmations or CAS-update existing teacher score locks.

        Transaction ownership belongs to the caller. The gateway below always
        invokes this method under one immediate transaction.
        """
        requested_session_id = _positive_integer(session_id, "session_id")
        requested_batch_id = _nonblank_text(scan_batch_id, "scan_batch_id")
        if not confirmations:
            return {
                "locks": [],
                "inserted": 0,
                "updated": 0,
                "synced_details": 0,
                "updated_results": 0,
            }
        if self.session.connection.execute(
            "SELECT 1 FROM grading_sessions WHERE id = ?",
            (requested_session_id,),
        ).fetchone() is None:
            raise TeacherScoreLockOwnershipError(
                "Teacher score lock session does not exist."
            )

        normalized: list[dict[str, Any]] = []
        seen_keys: set[tuple[int, str]] = set()
        for raw in confirmations:
            item = _normalize_teacher_score_confirmation(raw)
            key = (item["student_id"], item["question_id"])
            if key in seen_keys:
                raise ValueError(
                    "The same student and question cannot be confirmed twice "
                    "in one teacher score lock batch."
                )
            seen_keys.add(key)
            normalized.append(item)

        student_ids = sorted({item["student_id"] for item in normalized})
        placeholders = ",".join("?" for _ in student_ids)
        existing_students = {
            int(row["id"])
            for row in self.session.connection.execute(
                f"SELECT id FROM students WHERE id IN ({placeholders})",
                student_ids,
            ).fetchall()
        }
        missing_students = [
            student_id
            for student_id in student_ids
            if student_id not in existing_students
        ]
        if missing_students:
            raise TeacherScoreLockOwnershipError(
                f"Teacher score lock students do not exist: {missing_students}"
            )

        detail_targets: dict[tuple[int, str], dict[str, int] | None] = {}
        current_locks: dict[tuple[int, str], dict[str, Any] | None] = {}
        seen_detail_ids: set[int] = set()
        for item in normalized:
            key = (item["student_id"], item["question_id"])
            current = self.get_teacher_score_lock(
                requested_session_id,
                requested_batch_id,
                item["student_id"],
                item["question_id"],
            )
            current_locks[key] = current
            _validate_teacher_score_lock_revision(
                session_id=requested_session_id,
                scan_batch_id=requested_batch_id,
                item=item,
                current=current,
            )
            self._validate_teacher_score_source_owner(
                requested_session_id,
                item,
            )
            detail = self._resolve_teacher_score_detail(
                requested_session_id,
                item["student_id"],
                item["question_id"],
                detail_question_id=item["detail_question_id"],
                result_id=item["result_id"],
                detail_id=item["detail_id"],
                source_target_type=item["source_target_type"],
                source_target_id=item["source_target_id"],
            )
            if detail is not None:
                detail_id = int(detail["detail_id"])
                if detail_id in seen_detail_ids:
                    raise ValueError(
                        "The same session detail cannot be synchronized twice "
                        "in one teacher score lock batch."
                    )
                seen_detail_ids.add(detail_id)
            detail_targets[key] = detail

        inserted = 0
        updated = 0
        persisted_ids: list[int] = []
        for item in normalized:
            key = (item["student_id"], item["question_id"])
            current = current_locks[key]
            if item["expected_revision"] == 0:
                cursor = self.session.connection.execute(
                    """
                    INSERT INTO teacher_score_locks (
                        session_id, scan_batch_id, student_id, question_id,
                        score_awarded, max_score, deduction_reason,
                        source_target_type, source_target_id, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        requested_session_id,
                        requested_batch_id,
                        item["student_id"],
                        item["question_id"],
                        item["score_awarded"],
                        item["max_score"],
                        item["deduction_reason"],
                        item["source_target_type"],
                        item["source_target_id"],
                    ),
                )
                persisted_ids.append(int(cursor.lastrowid))
                inserted += 1
                continue

            cursor = self.session.connection.execute(
                """
                UPDATE teacher_score_locks
                SET score_awarded = ?,
                    max_score = ?,
                    deduction_reason = ?,
                    source_target_type = ?,
                    source_target_id = ?,
                    revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND revision = ?
                """,
                (
                    item["score_awarded"],
                    item["max_score"],
                    item["deduction_reason"],
                    item["source_target_type"],
                    item["source_target_id"],
                    int(current["id"]),
                    item["expected_revision"],
                ),
            )
            if cursor.rowcount != 1:
                latest = self.get_teacher_score_lock(
                    requested_session_id,
                    requested_batch_id,
                    item["student_id"],
                    item["question_id"],
                )
                raise TeacherScoreLockRevisionConflict(
                    session_id=requested_session_id,
                    scan_batch_id=requested_batch_id,
                    student_id=item["student_id"],
                    question_id=item["question_id"],
                    expected_revision=item["expected_revision"],
                    current_revision=(
                        None if latest is None else int(latest["revision"])
                    ),
                )
            persisted_ids.append(int(current["id"]))
            updated += 1

        affected_result_ids: set[int] = set()
        synced_details = 0
        if sync_existing_details:
            for item in normalized:
                detail = detail_targets[
                    (item["student_id"], item["question_id"])
                ]
                if detail is None:
                    continue
                cursor = self.session.connection.execute(
                    """
                    UPDATE session_details
                    SET score_awarded = ?, deduction_reason = ?
                    WHERE id = ?
                    """,
                    (
                        item["score_awarded"],
                        item["deduction_reason"],
                        int(detail["detail_id"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "Teacher score detail synchronization did not update "
                        "exactly one row."
                    )
                synced_details += 1
                affected_result_ids.add(int(detail["result_id"]))
            for result_id in sorted(affected_result_ids):
                self.recalculate_result_score(result_id)

        persisted_by_id = {
            int(row["id"]): row
            for row in self.session.connection.execute(
                f"""
                SELECT
                    id, session_id, scan_batch_id, student_id, question_id,
                    score_awarded, max_score, deduction_reason,
                    source_target_type, source_target_id, revision,
                    created_at, updated_at
                FROM teacher_score_locks
                WHERE id IN ({",".join("?" for _ in persisted_ids)})
                """,
                persisted_ids,
            ).fetchall()
        }
        return {
            "locks": [dict(persisted_by_id[lock_id]) for lock_id in persisted_ids],
            "inserted": inserted,
            "updated": updated,
            "synced_details": synced_details,
            "updated_results": len(affected_result_ids),
        }

    def delete_session_teacher_score_locks(self, session_id: int) -> int:
        cursor = self.session.connection.execute(
            "DELETE FROM teacher_score_locks WHERE session_id = ?",
            (int(session_id),),
        )
        return max(0, int(cursor.rowcount))

    def _resolve_teacher_score_detail(
        self,
        session_id: int,
        student_id: int,
        question_id: str,
        *,
        detail_question_id: str,
        result_id: int | None,
        detail_id: int | None,
        source_target_type: str,
        source_target_id: int,
    ) -> dict[str, int] | None:
        if result_id is not None and detail_id is not None:
            row = self.session.connection.execute(
                """
                SELECT
                    sd.id AS detail_id,
                    sd.result_id,
                    sd.question_id
                FROM session_details sd
                JOIN session_results sr ON sr.id = sd.result_id
                WHERE sd.id = ?
                  AND sd.result_id = ?
                  AND sr.session_id = ?
                  AND sr.student_id = ?
                """,
                (
                    int(detail_id),
                    int(result_id),
                    int(session_id),
                    int(student_id),
                ),
            ).fetchone()
            if (
                row is None
                or str(row["question_id"] or "").strip()
                != str(detail_question_id)
            ):
                raise TeacherScoreLockOwnershipError(
                    "Teacher score detail does not belong to the requested "
                    "session, student, result, and question."
                )
            if (
                source_target_type == "session_detail"
                and int(source_target_id) != int(detail_id)
            ):
                raise TeacherScoreLockOwnershipError(
                    "Teacher score source detail does not match detail_id."
                )
            return dict(row)

        if source_target_type != "session_detail":
            return None
        rows = self.session.connection.execute(
            """
            SELECT sd.id AS detail_id, sd.result_id
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            WHERE sr.session_id = ?
              AND sr.student_id = ?
              AND sd.question_id = ?
            ORDER BY sd.id
            """,
            (int(session_id), int(student_id), str(detail_question_id)),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError(
                "Multiple session details match one teacher score lock."
            )
        detail = dict(rows[0]) if rows else None
        if (
            detail is None or int(detail["detail_id"]) != int(source_target_id)
        ):
            raise TeacherScoreLockOwnershipError(
                "Teacher score source detail does not belong to the requested "
                "session, student, and question."
            )
        return detail

    def _validate_teacher_score_source_owner(
        self,
        session_id: int,
        item: dict[str, Any],
    ) -> None:
        target_type = item["source_target_type"]
        target_id = item["source_target_id"]
        if target_type == "answer_region":
            row = self.session.connection.execute(
                """
                SELECT session_id, mapped_question_id, detected_question_id
                FROM answer_regions
                WHERE id = ?
                """,
                (target_id,),
            ).fetchone()
            target_question = (
                str(row["mapped_question_id"] or row["detected_question_id"] or "").strip()
                if row is not None
                else ""
            )
            if (
                row is None
                or int(row["session_id"]) != int(session_id)
                or not _region_matches_question(
                    target_question,
                    item["question_id"],
                )
            ):
                raise TeacherScoreLockOwnershipError(
                    "Teacher score source region does not belong to the "
                    "requested session and question."
                )
        elif target_type == "exam_paper":
            row = self.session.connection.execute(
                """
                SELECT session_id, student_id
                FROM exam_papers
                WHERE id = ?
                """,
                (target_id,),
            ).fetchone()
            if (
                row is None
                or int(row["session_id"]) != int(session_id)
                or row["student_id"] is None
                or int(row["student_id"]) != item["student_id"]
            ):
                raise TeacherScoreLockOwnershipError(
                    "Teacher score source paper does not belong to the "
                    "requested session and student."
                )
        elif target_type != "session_detail":
            raise TeacherScoreLockOwnershipError(
                "Teacher score source type is not supported."
            )

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
                    error_category = ?, error_summary = ?,
                    ai_score_awarded = CASE
                        WHEN ai_score_awarded IS NULL
                          AND (
                              confidence_score IS NOT NULL
                              OR deduction_reason IS NOT NULL
                          )
                        THEN score_awarded
                        ELSE ai_score_awarded
                    END
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
                sr.student_id,
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
                SET score_awarded = ?,
                    ai_score_awarded = CASE
                        WHEN ai_score_awarded IS NULL
                          AND (
                              confidence_score IS NOT NULL
                              OR deduction_reason IS NOT NULL
                          )
                        THEN score_awarded
                        ELSE ai_score_awarded
                    END
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

    def list_teacher_score_locks(
        self,
        session_id: int,
        scan_batch_id: str | None = None,
        *,
        student_id: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._sessions.session(read_only=True) as session:
            return ReviewRepository(session).list_teacher_score_locks(
                session_id,
                scan_batch_id=scan_batch_id,
                student_id=student_id,
            )

    def get_teacher_score_lock(
        self,
        session_id: int,
        scan_batch_id: str,
        student_id: int,
        question_id: str,
    ) -> dict[str, Any] | None:
        with self._sessions.session(read_only=True) as session:
            return ReviewRepository(session).get_teacher_score_lock(
                session_id,
                scan_batch_id,
                student_id,
                question_id,
            )

    def confirm_teacher_score_lock(
        self,
        *,
        session_id: int,
        scan_batch_id: str,
        student_id: int,
        question_id: str,
        score_awarded: float,
        max_score: float,
        deduction_reason: str | None,
        source_target_type: str,
        source_target_id: int,
        expected_revision: int,
        sync_existing_detail: bool = True,
    ) -> dict[str, Any]:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                return ReviewRepository(session).confirm_teacher_score_lock(
                    session_id=session_id,
                    scan_batch_id=scan_batch_id,
                    student_id=student_id,
                    question_id=question_id,
                    score_awarded=score_awarded,
                    max_score=max_score,
                    deduction_reason=deduction_reason,
                    source_target_type=source_target_type,
                    source_target_id=source_target_id,
                    expected_revision=expected_revision,
                    sync_existing_detail=sync_existing_detail,
                )

    def confirm_teacher_scores(
        self,
        session_id: int,
        scan_batch_id: str,
        confirmations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.confirm_teacher_score_locks(
            session_id,
            scan_batch_id,
            confirmations,
        )

    def confirm_teacher_score_locks(
        self,
        session_id: int,
        scan_batch_id: str,
        confirmations: list[dict[str, Any]],
        *,
        sync_existing_details: bool = True,
    ) -> dict[str, Any]:
        if not confirmations:
            return {
                "locks": [],
                "inserted": 0,
                "updated": 0,
                "synced_details": 0,
                "updated_results": 0,
            }
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                return ReviewRepository(session).confirm_teacher_score_locks(
                    session_id,
                    scan_batch_id,
                    confirmations,
                    sync_existing_details=sync_existing_details,
                )

    def delete_session_teacher_score_locks(self, session_id: int) -> int:
        with self._sessions.session() as session:
            with session.transaction(immediate=True):
                return ReviewRepository(
                    session
                ).delete_session_teacher_score_locks(session_id)

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
