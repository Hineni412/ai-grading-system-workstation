from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ReadOnlyQuestionEvidenceReader:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def list_questions(self) -> list[dict[str, Any]]:
        if not self.database_path.is_file():
            return []
        try:
            with _read_only_connection(
                self.database_path,
                required_tables={"questions", "question_tags"},
            ) as connection:
                rows = connection.execute(
                    """
                    SELECT
                        id,
                        question_number,
                        question_type,
                        question_text,
                        difficulty,
                        updated_at
                    FROM questions
                    WHERE COALESCE(is_deleted, 0) = 0
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 100
                    """
                ).fetchall()
                tags = _knowledge_tags(
                    connection,
                    [int(row["id"]) for row in rows],
                )
        except (OSError, sqlite3.DatabaseError):
            return []
        return [
            {
                "question_id": int(row["id"]),
                "question_number": str(row["question_number"]),
                "question_type": (
                    str(row["question_type"])
                    if row["question_type"] is not None
                    else None
                ),
                "text_excerpt": str(row["question_text"])[:160],
                "difficulty": (
                    str(row["difficulty"])
                    if row["difficulty"] is not None
                    else None
                ),
                "knowledge_points": tags.get(int(row["id"]), []),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def read(self, query: dict[str, Any]) -> dict[str, Any]:
        question_ids = _positive_ids(query.get("question_ids"))
        if not question_ids:
            return _empty_snapshot(
                kind="question",
                availability="not_selected",
                missing=["no_selected_questions"],
            )
        if not self.database_path.is_file():
            return _empty_snapshot(
                kind="question",
                availability="unavailable",
                missing=["question_bank_unavailable"],
            )
        try:
            with _read_only_connection(
                self.database_path,
                required_tables={"questions", "question_tags"},
            ) as connection:
                placeholders = ",".join("?" for _ in question_ids)
                rows = connection.execute(
                    f"""
                    SELECT
                        id,
                        question_number,
                        question_type,
                        question_text,
                        answer_text,
                        difficulty,
                        needs_review,
                        has_images,
                        image_paths,
                        updated_at
                    FROM questions
                    WHERE id IN ({placeholders})
                      AND COALESCE(is_deleted, 0) = 0
                    ORDER BY id
                    """,
                    question_ids,
                ).fetchall()
                tags = _knowledge_tags(
                    connection,
                    [int(row["id"]) for row in rows],
                )
        except (OSError, sqlite3.DatabaseError):
            return _empty_snapshot(
                kind="question",
                availability="unavailable",
                missing=["question_bank_unavailable"],
            )
        found_ids = {int(row["id"]) for row in rows}
        items = [
            {
                "question_id": int(row["id"]),
                "question_number": str(row["question_number"]),
                "question_type": (
                    str(row["question_type"])
                    if row["question_type"] is not None
                    else None
                ),
                "question_text": str(row["question_text"]),
                "answer_text": (
                    str(row["answer_text"])
                    if row["answer_text"] is not None
                    else None
                ),
                "difficulty": (
                    str(row["difficulty"])
                    if row["difficulty"] is not None
                    else None
                ),
                "knowledge_points": tags.get(int(row["id"]), []),
                "needs_review": bool(row["needs_review"]),
                "image_paths": (
                    str(row["image_paths"]) if row["image_paths"] else None
                ),
                "source_revision": _digest(
                    {
                        "id": int(row["id"]),
                        "updated_at": str(row["updated_at"]),
                        "knowledge_points": tags.get(int(row["id"]), []),
                    }
                ),
            }
            for row in rows
        ]
        missing_ids = [
            question_id
            for question_id in question_ids
            if question_id not in found_ids
        ]
        snapshot: dict[str, Any] = {
            "kind": "question",
            "availability": "ready",
            "selected_count": len(question_ids),
            "item_count": len(items),
            "coverage": (
                round(len(items) / len(question_ids), 4)
                if question_ids
                else 0.0
            ),
            "items": items,
            "missing": (
                [{"code": "question_not_found", "question_ids": missing_ids}]
                if missing_ids
                else []
            ),
        }
        snapshot["source_version"] = _digest(snapshot)
        return snapshot


class ReadOnlyAssessmentEvidenceReader:
    def __init__(
        self,
        grading_database_path: str | Path,
        question_database_path: str | Path,
    ) -> None:
        self.grading_database_path = Path(grading_database_path)
        self.question_database_path = Path(question_database_path)

    def list_assessments(self) -> list[dict[str, Any]]:
        if not self.grading_database_path.is_file():
            return []
        try:
            with _read_only_connection(
                self.grading_database_path,
                required_tables={
                    "grading_sessions",
                    "session_results",
                    "students",
                },
            ) as connection:
                rows = connection.execute(
                    """
                    SELECT
                        gs.id,
                        gs.session_name,
                        gs.status,
                        gs.created_at,
                        gs.updated_at,
                        s.class_name,
                        COUNT(DISTINCT sr.student_id) AS student_count
                    FROM grading_sessions AS gs
                    LEFT JOIN session_results AS sr
                        ON sr.session_id = gs.id
                    LEFT JOIN students AS s
                        ON s.id = sr.student_id
                    WHERE COALESCE(gs.is_deleted, 0) = 0
                    GROUP BY
                        gs.id,
                        gs.session_name,
                        gs.status,
                        gs.created_at,
                        gs.updated_at,
                        s.class_name
                    ORDER BY gs.id DESC, s.class_name
                    """
                ).fetchall()
        except (OSError, sqlite3.DatabaseError):
            return []
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            session_id = int(row["id"])
            item = grouped.setdefault(
                session_id,
                {
                    "assessment_id": session_id,
                    "title": str(row["session_name"]),
                    "status": str(row["status"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "classes": [],
                },
            )
            class_name = str(row["class_name"] or "").strip()
            if class_name:
                item["classes"].append(
                    {
                        "class_name": class_name,
                        "student_count": int(row["student_count"] or 0),
                    }
                )
        return list(grouped.values())

    def read(self, query: dict[str, Any]) -> dict[str, Any]:
        session_ids = _positive_ids(query.get("assessment_ids"))
        class_name = str(query.get("class_name") or "").strip()
        knowledge_scope = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in (query.get("knowledge_scope") or [])
                if str(value).strip()
            )
        )
        if not session_ids:
            return _empty_snapshot(
                kind="assessment",
                availability="not_selected",
                missing=["no_selected_assessments"],
                extra={"class_name": class_name, "knowledge_scope": list(knowledge_scope)},
            )
        if not class_name:
            return _empty_snapshot(
                kind="assessment",
                availability="invalid",
                missing=["class_name_required"],
                extra={"class_name": "", "knowledge_scope": list(knowledge_scope)},
            )
        if not self.grading_database_path.is_file():
            return _empty_snapshot(
                kind="assessment",
                availability="unavailable",
                missing=["grading_database_unavailable"],
                extra={"class_name": class_name, "knowledge_scope": list(knowledge_scope)},
            )
        try:
            with _read_only_connection(
                self.grading_database_path,
                required_tables={
                    "grading_sessions",
                    "session_details",
                    "session_results",
                    "students",
                },
            ) as grading:
                session_rows, item_rows = _assessment_rows(
                    grading,
                    session_ids=session_ids,
                    class_name=class_name,
                )
            link_map = self._confirmed_link_map(session_ids)
        except (OSError, sqlite3.DatabaseError):
            return _empty_snapshot(
                kind="assessment",
                availability="unavailable",
                missing=["assessment_evidence_unavailable"],
                extra={"class_name": class_name, "knowledge_scope": list(knowledge_scope)},
            )
        selected_sessions = {int(row["session_id"]) for row in session_rows}
        missing_sessions = [
            session_id
            for session_id in session_ids
            if session_id not in selected_sessions
        ]
        knowledge_summary: dict[str, dict[str, Any]] = {}
        linked_tagged: set[tuple[int, str]] = set()
        missing_link_count = 0
        missing_tag_count = 0
        for row in item_rows:
            key = (int(row["session_id"]), str(row["question_id"]))
            linked = link_map.get(key)
            if linked is None:
                missing_link_count += 1
                continue
            tags = linked["knowledge_points"]
            if not tags:
                missing_tag_count += 1
                continue
            relevant_tags = [
                tag
                for tag in tags
                if not knowledge_scope or tag in knowledge_scope
            ]
            if not relevant_tags:
                continue
            linked_tagged.add(key)
            for tag in relevant_tags:
                entry = knowledge_summary.setdefault(
                    tag,
                    {
                        "knowledge_point": tag,
                        "question_count": 0,
                        "observation_count": 0,
                        "error_observation_count": 0,
                        "_questions": set(),
                    },
                )
                entry["_questions"].add(key)
                entry["observation_count"] += int(row["observation_count"])
                entry["error_observation_count"] += int(
                    row["error_observation_count"]
                )
        summaries: list[dict[str, Any]] = []
        for entry in knowledge_summary.values():
            question_count = len(entry.pop("_questions"))
            observations = int(entry["observation_count"])
            errors = int(entry["error_observation_count"])
            entry["question_count"] = question_count
            entry["observed_error_rate"] = (
                round(errors / observations, 4) if observations else 0.0
            )
            summaries.append(entry)
        summaries.sort(key=lambda item: str(item["knowledge_point"]))
        total_questions = sum(int(row["question_count"]) for row in session_rows)
        student_count = max(
            (int(row["student_count"]) for row in session_rows),
            default=0,
        )
        missing: list[dict[str, Any]] = []
        if missing_sessions:
            missing.append(
                {
                    "code": "assessment_or_class_not_found",
                    "assessment_ids": missing_sessions,
                }
            )
        if missing_link_count:
            missing.append(
                {
                    "code": "question_bank_link_missing",
                    "question_count": missing_link_count,
                }
            )
        if missing_tag_count:
            missing.append(
                {
                    "code": "exact_knowledge_tag_missing",
                    "question_count": missing_tag_count,
                }
            )
        if not summaries:
            missing.append({"code": "no_matching_knowledge_evidence"})
        snapshot = {
            "kind": "assessment",
            "availability": "ready",
            "class_name": class_name,
            "knowledge_scope": list(knowledge_scope),
            "assessment_count": len(session_rows),
            "student_count": student_count,
            "question_count": total_questions,
            "covered_question_count": len(linked_tagged),
            "coverage": (
                round(len(linked_tagged) / total_questions, 4)
                if total_questions
                else 0.0
            ),
            "assessments": [
                {
                    "assessment_id": int(row["session_id"]),
                    "title": str(row["session_name"]),
                    "assessment_date": str(
                        row["assessment_date"] or row["updated_at"]
                    ),
                    "student_count": int(row["student_count"]),
                    "question_count": int(row["question_count"]),
                }
                for row in session_rows
            ],
            "knowledge_summary": summaries,
            "missing": missing,
        }
        snapshot["source_version"] = _digest(snapshot)
        return snapshot

    def _confirmed_link_map(
        self,
        session_ids: list[int],
    ) -> dict[tuple[int, str], dict[str, Any]]:
        if not self.question_database_path.is_file():
            return {}
        with _read_only_connection(
            self.question_database_path,
            required_tables={
                "grading_question_links",
                "question_tags",
                "questions",
            },
        ) as connection:
            placeholders = ",".join("?" for _ in session_ids)
            rows = connection.execute(
                f"""
                SELECT
                    link.grading_session_id,
                    link.source_question_id,
                    link.bank_question_id,
                    tag.tag_value
                FROM grading_question_links AS link
                JOIN questions AS question
                    ON question.id = link.bank_question_id
                   AND COALESCE(question.is_deleted, 0) = 0
                LEFT JOIN question_tags AS tag
                    ON tag.question_id = question.id
                   AND tag.tag_type = 'knowledge_point'
                   AND TRIM(tag.tag_value) <> ''
                WHERE link.status = 'confirmed'
                  AND CAST(link.grading_session_id AS INTEGER)
                      IN ({placeholders})
                ORDER BY
                    link.grading_session_id,
                    link.source_question_id,
                    tag.tag_value
                """,
                session_ids,
            ).fetchall()
        result: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                int(row["grading_session_id"]),
                str(row["source_question_id"]),
            )
            entry = result.setdefault(
                key,
                {
                    "bank_question_id": int(row["bank_question_id"]),
                    "knowledge_points": [],
                },
            )
            tag = str(row["tag_value"] or "").strip()
            if tag and tag not in entry["knowledge_points"]:
                entry["knowledge_points"].append(tag)
        return result


def _assessment_rows(
    connection: sqlite3.Connection,
    *,
    session_ids: list[int],
    class_name: str,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    placeholders = ",".join("?" for _ in session_ids)
    parameters: tuple[object, ...] = (*session_ids, class_name)
    sessions = connection.execute(
        f"""
        SELECT
            gs.id AS session_id,
            gs.session_name,
            gs.updated_at,
            MAX(sr.graded_at) AS assessment_date,
            COUNT(DISTINCT sr.student_id) AS student_count,
            COUNT(DISTINCT sd.question_id) AS question_count
        FROM grading_sessions AS gs
        JOIN session_results AS sr
            ON sr.session_id = gs.id
        JOIN students AS student
            ON student.id = sr.student_id
        JOIN session_details AS sd
            ON sd.result_id = sr.id
        WHERE COALESCE(gs.is_deleted, 0) = 0
          AND gs.id IN ({placeholders})
          AND student.class_name = ?
        GROUP BY gs.id, gs.session_name, gs.updated_at
        ORDER BY gs.id
        """,
        parameters,
    ).fetchall()
    items = connection.execute(
        f"""
        SELECT
            gs.id AS session_id,
            sd.question_id,
            COUNT(*) AS observation_count,
            SUM(
                CASE
                    WHEN COALESCE(TRIM(sd.deduction_reason), '') <> ''
                      OR COALESCE(TRIM(sd.error_category), '') <> ''
                      OR sd.score_awarded <= 0
                    THEN 1
                    ELSE 0
                END
            ) AS error_observation_count
        FROM grading_sessions AS gs
        JOIN session_results AS sr
            ON sr.session_id = gs.id
        JOIN students AS student
            ON student.id = sr.student_id
        JOIN session_details AS sd
            ON sd.result_id = sr.id
        WHERE COALESCE(gs.is_deleted, 0) = 0
          AND gs.id IN ({placeholders})
          AND student.class_name = ?
        GROUP BY gs.id, sd.question_id
        ORDER BY gs.id, sd.question_id
        """,
        parameters,
    ).fetchall()
    return sessions, items


def _knowledge_tags(
    connection: sqlite3.Connection,
    question_ids: list[int],
) -> dict[int, list[str]]:
    if not question_ids:
        return {}
    placeholders = ",".join("?" for _ in question_ids)
    rows = connection.execute(
        f"""
        SELECT question_id, tag_value
        FROM question_tags
        WHERE question_id IN ({placeholders})
          AND tag_type = 'knowledge_point'
          AND TRIM(tag_value) <> ''
        ORDER BY question_id, tag_value
        """,
        question_ids,
    ).fetchall()
    result: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        value = str(row["tag_value"])
        if value not in result[int(row["question_id"])]:
            result[int(row["question_id"])].append(value)
    return dict(result)


@contextmanager
def _read_only_connection(
    path: Path,
    *,
    required_tables: set[str],
) -> Iterator[sqlite3.Connection]:
    uri = f"{path.resolve(strict=True).as_uri()}?mode=ro"
    connection = sqlite3.connect(
        uri,
        uri=True,
        isolation_level=None,
        timeout=5.0,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        available = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not required_tables.issubset(available):
            raise sqlite3.DatabaseError("required evidence tables are unavailable")
        connection.execute("BEGIN")
        yield connection
        connection.rollback()
    finally:
        connection.close()


def _positive_ids(value: object) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int] = []
    for item in value:
        try:
            identifier = int(item)
        except (TypeError, ValueError):
            continue
        if identifier > 0 and identifier not in result:
            result.append(identifier)
    return result[:500]


def _empty_snapshot(
    *,
    kind: str,
    availability: str,
    missing: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "kind": kind,
        "availability": availability,
        "coverage": 0.0,
        "items": [],
        "missing": [{"code": code} for code in missing],
    }
    snapshot.update(extra or {})
    snapshot["source_version"] = _digest(snapshot)
    return snapshot


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
