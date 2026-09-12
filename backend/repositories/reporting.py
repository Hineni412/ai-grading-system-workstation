"""Consistent read snapshots for session report exports."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.repositories.base import RepositorySession, RepositorySessionProvider


@dataclass(frozen=True, slots=True)
class SessionReportSnapshot:
    session: dict[str, Any] | None
    results: list[dict[str, Any]]
    attendance: list[dict[str, Any]]
    details: list[dict[str, Any]]
    locks: list[dict[str, Any]]
    # Read-only backfill from the question bank: question_id -> knowledge
    # point entries ({"path": full hierarchical tag, "label": leaf segment}).
    # Empty when the question bank is unavailable or the session is unlinked.
    knowledge_backfill: dict[str, list[dict[str, str]]] = field(
        default_factory=dict
    )
    question_assessments: dict[str, dict[str, Any]] = field(default_factory=dict)


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
                sr.student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sr.total_score,
                sr.student_score,
                sr.ai_student_score,
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
                sr.student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                sd.question_id,
                sd.score_awarded,
                sd.ai_score_awarded,
                sd.deduction_reason,
                sd.knowledge_ids,
                sd.error_category,
                sd.error_summary,
                sd.confidence_score
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            JOIN students s ON s.id = sr.student_id
            WHERE sr.session_id = ?
            ORDER BY sr.id ASC, sd.id ASC
            """,
            (int(session_id),),
        ).fetchall()
        lock_rows = connection.execute(
            """
            SELECT
                tsl.student_id,
                s.student_code,
                s.name AS student_name,
                s.class_name,
                tsl.scan_batch_id,
                tsl.question_id,
                tsl.score_awarded,
                tsl.max_score,
                tsl.deduction_reason,
                tsl.revision,
                tsl.updated_at
            FROM teacher_score_locks tsl
            JOIN students s ON s.id = tsl.student_id
            WHERE tsl.session_id = ?
            ORDER BY tsl.student_id ASC, tsl.question_id ASC, tsl.id ASC
            """,
            (int(session_id),),
        ).fetchall()
        results = [dict(row) for row in result_rows]
        details = [_detail_with_knowledge_ids(row) for row in detail_rows]
        locks = [dict(row) for row in lock_rows]
        result_student_ids = {int(row["student_id"]) for row in results}
        manual_locks = [
            lock for lock in locks
            if int(lock["student_id"]) not in result_student_ids
        ]
        if manual_locks:
            # Students graded entirely by the teacher have no AI result row,
            # including in sessions where other students used AI grading.
            manual_results, manual_details = _synthesize_manual_results(manual_locks)
            results.extend(manual_results)
            details.extend(manual_details)
        return SessionReportSnapshot(
            session=dict(session_row) if session_row is not None else None,
            results=results,
            attendance=[dict(row) for row in attendance_rows],
            details=details,
            locks=locks,
        )


def _synthesize_manual_results(
    locks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    locks_by_student: dict[int, list[dict[str, Any]]] = {}
    for lock in locks:
        locks_by_student.setdefault(int(lock["student_id"]), []).append(lock)
    for student_id in sorted(locks_by_student):
        student_locks = locks_by_student[student_id]
        first = student_locks[0]
        # Negative synthetic ids cannot collide with real AUTOINCREMENT ids
        # and stay stable for one snapshot.
        result_id = -student_id
        results.append(
            {
                "result_id": result_id,
                "student_id": student_id,
                "student_code": first["student_code"],
                "student_name": first["student_name"],
                "class_name": first["class_name"],
                "total_score": float(
                    sum(float(lock["max_score"]) for lock in student_locks)
                ),
                "student_score": float(
                    sum(float(lock["score_awarded"]) for lock in student_locks)
                ),
                "ai_student_score": None,
                "needs_human_review": 0,
                "graded_at": max(
                    str(lock["updated_at"] or "") for lock in student_locks
                ),
                "raw_json": "{}",
            }
        )
        for lock in student_locks:
            details.append(
                {
                    "result_id": result_id,
                    "student_id": student_id,
                    "student_code": lock["student_code"],
                    "student_name": lock["student_name"],
                    "class_name": lock["class_name"],
                    "question_id": str(lock["question_id"]),
                    "score_awarded": float(lock["score_awarded"]),
                    "ai_score_awarded": None,
                    "deduction_reason": lock["deduction_reason"],
                    "knowledge_ids": ["UNKNOWN"],
                    "knowledge_id": "UNKNOWN",
                    "error_category": "教师已确认",
                    "error_summary": "teacher_score_locked",
                }
            )
    return results, details


def _detail_with_knowledge_ids(row: Any) -> dict[str, Any]:
    detail = dict(row)
    raw_ids = detail.get("knowledge_ids")
    knowledge_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else list(raw_ids)
    detail["knowledge_ids"] = knowledge_ids
    detail["knowledge_id"] = knowledge_ids[0]
    return detail


class ReportRepositoryGateway:
    """Open one consistent read transaction per report snapshot."""

    def __init__(self, sessions: RepositorySessionProvider) -> None:
        self._sessions = sessions

    def get_session_report_snapshot(
        self,
        session_id: int,
        *,
        question_bank_path: Path | None = None,
    ) -> SessionReportSnapshot:
        with self._sessions.session(read_only=True) as session:
            with session.transaction():
                snapshot = ReportRepository(session).session_report_snapshot(
                    session_id
                )
        backfill: dict[str, list[dict[str, str]]] = {}
        assessments: dict[str, dict[str, Any]] = {}
        if question_bank_path is not None:
            backfill = load_question_bank_knowledge_backfill(
                Path(question_bank_path),
                int(session_id),
            )
            from path_manager import resolve_stored_file_path
            from question_id_contract import canonicalize_question_document
            try:
                rubric_path = resolve_stored_file_path(
                    (snapshot.session or {}).get("rubric_path"),
                    data_root=Path(question_bank_path).parent.parent,
                )
                rubric = canonicalize_question_document(json.loads(rubric_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                rubric = {}
            overrides, assessments = load_question_bank_part_context(
                Path(question_bank_path), int(session_id), rubric,
            )
            backfill.update(overrides)
        if not backfill:
            return snapshot
        return SessionReportSnapshot(
            session=snapshot.session,
            results=snapshot.results,
            attendance=snapshot.attendance,
            details=snapshot.details,
            locks=snapshot.locks,
            knowledge_backfill=backfill,
            question_assessments=assessments,
        )

    def get_session_rubric_path(self, session_id: int) -> str | None:
        with self._sessions.session(read_only=True) as session:
            return ReportRepository(session).get_session_rubric_path(session_id)


def load_question_bank_knowledge_backfill(
    question_bank_path: Path,
    session_id: int,
) -> dict[str, list[dict[str, str]]]:
    """Read confirmed question-bank knowledge tags for one grading session.

    The question bank is a separate module database; this is a read-only
    lookup keyed by ``grading_question_links.grading_session_id``.  Each
    entry carries the full hierarchical tag path plus its leaf label, so
    callers can bucket by path while displaying the label.  Any failure to
    open or read the question bank yields an empty mapping rather than
    breaking report export.
    """
    path = Path(question_bank_path)
    if not path.is_file():
        return {}
    try:
        connection = sqlite3.connect(
            f"{path.resolve(strict=True).as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=5.0,
        )
    except (OSError, sqlite3.Error):
        return {}
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        available = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {"grading_question_links", "question_tags", "questions"}.issubset(
            available
        ):
            return {}
        rows = connection.execute(
            """
            SELECT
                link.source_question_id,
                tag.tag_value
            FROM grading_question_links AS link
            JOIN questions AS question
                ON question.id = link.bank_question_id
               AND COALESCE(question.is_deleted, 0) = 0
            JOIN question_tags AS tag
                ON tag.question_id = question.id
               AND tag.tag_type = 'knowledge_point'
               AND TRIM(tag.tag_value) <> ''
            WHERE link.status = 'confirmed'
              AND CAST(link.grading_session_id AS INTEGER) = ?
            ORDER BY link.source_question_id, tag.tag_value
            """,
            (int(session_id),),
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    backfill: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        question_id = str(row["source_question_id"] or "").strip()
        tag_path = str(row["tag_value"] or "").strip()
        if not question_id or not tag_path:
            continue
        label = _knowledge_leaf_label(tag_path)
        entries = backfill.setdefault(question_id, [])
        if all(entry["path"] != tag_path for entry in entries):
            entries.append({"path": tag_path, "label": label})
    return backfill


def _knowledge_leaf_label(tag_path: str) -> str:
    """Leaf segment of a hierarchical knowledge path (full-width separators)."""
    segments = [
        segment.strip()
        for segment in str(tag_path or "").replace("|", "｜").split("｜")
    ]
    for segment in reversed(segments):
        if segment:
            return segment
    return ""


def load_question_bank_part_context(
    question_bank_path: Path, session_id: int, rubric: dict[str, Any],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, Any]]]:
    """Use the same exact part/source matching as the knowledge heatmap."""
    from integration.question_tag_projection_service import QuestionTagProjectionService
    from question_bank.solution_evidence.part_assessments import reading
    if not question_bank_path.is_file():
        return {}, {}
    try:
        with reading(question_bank_path) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_part_assessment_profiles'").fetchone() is None:
                return {}, {}
            overrides = {
                str(row[0]): [] for row in connection.execute(
                    """SELECT link.source_question_id FROM grading_question_links link
                       JOIN question_part_assessment_profiles p ON p.question_id=link.bank_question_id AND p.status='active'
                       WHERE link.status='confirmed' AND CAST(link.grading_session_id AS INTEGER)=?""", (session_id,),
                )
            }
            # Missing rubric/parts cannot justify falling back to parent tags.
            if not rubric:
                return overrides, {}
            projection = QuestionTagProjectionService(
                question_bank_path, external_connection=connection,
            ).project_session(grading_session_id=session_id, rubric=rubric)
            names = {
                str(row["stable_key"]): str(row["display_name"])
                for row in connection.execute("SELECT stable_key,display_name FROM knowledge_tag_identities")
            }
        assessments = {}
        for item in projection.items:
            if item.assessment.get("granularity") != "part":
                continue
            # An explicit empty list prevents old parent/rubric tags from being
            # spread across parts whose historical source cannot be matched.
            overrides[item.item_ref] = [
                {"path": names.get(key, key), "label": _knowledge_leaf_label(names.get(key, key)), "stable_key": key}
                for key in item.tags.get("knowledge_point", ())
            ]
            assessments[item.item_ref] = dict(item.assessment)
        return overrides, assessments
    except (sqlite3.Error, OSError, ValueError, KeyError):
        return {}, {}


__all__ = [
    "ReportRepository",
    "ReportRepositoryGateway",
    "SessionReportSnapshot",
    "load_question_bank_knowledge_backfill",
]
