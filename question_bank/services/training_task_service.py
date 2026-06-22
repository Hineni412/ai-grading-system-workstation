from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from question_bank.database.schema import connect, initialize_database


@dataclass(frozen=True, slots=True)
class TrainingTaskItemRecord:
    id: int
    task_item_code: str
    question_id: int | None
    item_order: int
    stage: str


@dataclass(frozen=True, slots=True)
class TrainingVariantRecord:
    id: int
    variant_key: str
    variant_type: str
    student_ids: tuple[str, ...]
    items: tuple[TrainingTaskItemRecord, ...]


@dataclass(frozen=True, slots=True)
class TrainingTaskRecord:
    id: int
    task_code: str
    status: str
    variants: tuple[TrainingVariantRecord, ...]


class TrainingTaskService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def create_task(
        self,
        practice_plan: Mapping[str, Any],
        *,
        created_by: str | None = None,
    ) -> TrainingTaskRecord:
        initialize_database(self.db_path)
        task_code = _new_task_code()
        scope_snapshot = practice_plan.get("scope_snapshot") or practice_plan.get("scope") or {}
        exam_scope = practice_plan.get("exam_scope") or {}
        diagnosis_snapshot = practice_plan.get("diagnosis_snapshot") or {}
        generation_config = practice_plan.get("generation_config") or {}
        warnings = practice_plan.get("warnings") or []
        variants = practice_plan.get("variants") or []
        if not isinstance(variants, list) or not variants:
            raise ValueError("practice plan must contain at least one variant")
        student_index = _student_index(diagnosis_snapshot)

        with connect(self.db_path) as conn:
            task_cursor = conn.execute(
                """
                INSERT INTO training_tasks (
                    task_code, created_by, scope_json, exam_scope_json,
                    diagnosis_snapshot_json, generation_config_json, warnings_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready')
                """,
                (
                    task_code,
                    _optional_text(created_by),
                    _json(scope_snapshot),
                    _json(exam_scope),
                    _json(diagnosis_snapshot),
                    _json(generation_config),
                    _json(warnings),
                ),
            )
            task_id = int(task_cursor.lastrowid)
            for variant_index, variant in enumerate(variants, start=1):
                if not isinstance(variant, Mapping):
                    raise ValueError("training variant must be a mapping")
                variant_key = str(variant.get("variant_key") or f"variant-{variant_index}")
                variant_type = str(variant.get("variant_type") or "individual")
                variant_cursor = conn.execute(
                    """
                    INSERT INTO training_variants (
                        task_id, variant_key, variant_type, grouping_reason_json,
                        diagnosis_snapshot_json, shortages_json, warnings_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready')
                    """,
                    (
                        task_id,
                        variant_key,
                        variant_type,
                        _json(variant.get("grouping_reason") or {}),
                        _json(variant.get("diagnosis_snapshot") or {}),
                        _json(variant.get("shortages") or []),
                        _json(variant.get("warnings") or []),
                    ),
                )
                variant_id = int(variant_cursor.lastrowid)
                variant_students = [str(value) for value in variant.get("student_ids", [])]
                for student_id in variant_students:
                    snapshot = student_index.get(student_id, {})
                    conn.execute(
                        """
                        INSERT INTO variant_students (
                            variant_id, student_id, student_name_snapshot, class_id_snapshot
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            variant_id,
                            student_id,
                            _optional_text(snapshot.get("student_name")),
                            _optional_text(snapshot.get("class_id") or snapshot.get("class_name")),
                        ),
                    )
                items = variant.get("items") or []
                for fallback_order, item in enumerate(items, start=1):
                    if not isinstance(item, Mapping):
                        raise ValueError("training task item must be a mapping")
                    item_order = int(item.get("item_order") or fallback_order)
                    task_item_code = f"{task_code}-V{variant_index:02d}-Q{item_order:02d}"
                    question_id = _optional_int(item.get("question_id") or item.get("bank_question_id"))
                    question_snapshot = _question_snapshot(conn, question_id, item)
                    concept_snapshot = {
                        key: item.get(key)
                        for key in (
                            "stage",
                            "target_concept_id",
                            "matched_concept_id",
                            "relation_type",
                            "concept_name",
                            "target_skill_id",
                            "matched_skill_id",
                            "target_skill_name",
                            "matched_skill_name",
                            "match_kind",
                            "neighbor_kind",
                            "reason",
                        )
                        if item.get(key) is not None
                    }
                    item_cursor = conn.execute(
                        """
                        INSERT INTO training_task_items (
                            variant_id, task_item_code, bank_question_id, bank_question_fingerprint,
                            item_order, stage, concept_snapshot_json,
                            recommendation_snapshot_json, question_snapshot_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            variant_id,
                            task_item_code,
                            question_id,
                            _optional_text(item.get("question_fingerprint") or item.get("bank_question_fingerprint")),
                            item_order,
                            str(item.get("stage") or "direct"),
                            _json(concept_snapshot),
                            _json(dict(item)),
                            _json(question_snapshot),
                        ),
                    )
                    if item_cursor.lastrowid is None:
                        raise RuntimeError("unable to create training task item")
        return self._record_for_task(task_id)

    def get_task(self, task_id: int) -> dict[str, Any]:
        with _read_connection(self.db_path) as conn:
            task = conn.execute(
                "SELECT * FROM training_tasks WHERE id = ?",
                (int(task_id),),
            ).fetchone()
            if task is None:
                raise KeyError(f"training task not found: {task_id}")
            result = _task_from_row(task)
            variant_rows = conn.execute(
                "SELECT * FROM training_variants WHERE task_id = ? ORDER BY id",
                (int(task_id),),
            ).fetchall()
            result["variants"] = [
                _variant_payload(conn, row)
                for row in variant_rows
            ]
            result["exports"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM training_exports WHERE task_id = ? ORDER BY id",
                    (int(task_id),),
                ).fetchall()
            ]
            return result

    def list_tasks(self) -> list[dict[str, Any]]:
        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM training_tasks ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def cancel_task(self, task_id: int) -> bool:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE training_tasks
                SET status = 'cancelled', updated_at = datetime('now','localtime')
                WHERE id = ? AND status <> 'cancelled'
                """,
                (int(task_id),),
            )
            return cursor.rowcount > 0

    def mark_export_state(
        self,
        task_id: int,
        *,
        status: str,
        variant_id: int | None = None,
    ) -> bool:
        task_statuses = {"ready", "exporting", "completed", "failed"}
        variant_statuses = {"ready", "exporting", "completed", "failed"}
        if status not in task_statuses:
            raise ValueError(f"unsupported export state: {status}")
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            task_cursor = conn.execute(
                """
                UPDATE training_tasks
                SET status = ?, updated_at = datetime('now','localtime')
                WHERE id = ? AND status <> 'cancelled'
                """,
                (status, int(task_id)),
            )
            if variant_id is not None:
                if status not in variant_statuses:
                    raise ValueError(f"unsupported variant export state: {status}")
                conn.execute(
                    """
                    UPDATE training_variants
                    SET status = ?, updated_at = datetime('now','localtime')
                    WHERE id = ? AND task_id = ?
                    """,
                    (status, int(variant_id), int(task_id)),
                )
            return task_cursor.rowcount > 0

    def record_attempt_stub(
        self,
        *,
        task_item_code: str,
        student_id: str | int,
        grading_session_id: str | int | None = None,
        grading_question_id: str | int | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> int:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO training_attempts (
                    task_item_code, student_id, grading_session_id,
                    grading_question_id, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(task_item_code),
                    str(student_id),
                    _optional_text(grading_session_id),
                    _optional_text(grading_question_id),
                    _json(evidence or {}),
                ),
            )
            return int(cursor.lastrowid)

    def list_attempts(self, task_id: int) -> list[dict[str, Any]]:
        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT a.*
                FROM training_attempts a
                JOIN training_task_items i ON i.task_item_code = a.task_item_code
                JOIN training_variants v ON v.id = i.variant_id
                WHERE v.task_id = ?
                ORDER BY a.id
                """,
                (int(task_id),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["evidence"] = _json_value(item.pop("evidence_json"), {})
            result.append(item)
        return result

    def _record_for_task(self, task_id: int) -> TrainingTaskRecord:
        loaded = self.get_task(task_id)
        return TrainingTaskRecord(
            id=int(loaded["id"]),
            task_code=str(loaded["task_code"]),
            status=str(loaded["status"]),
            variants=tuple(
                TrainingVariantRecord(
                    id=int(variant["id"]),
                    variant_key=str(variant["variant_key"]),
                    variant_type=str(variant["variant_type"]),
                    student_ids=tuple(item["student_id"] for item in variant["students"]),
                    items=tuple(
                        TrainingTaskItemRecord(
                            id=int(item["id"]),
                            task_item_code=str(item["task_item_code"]),
                            question_id=_optional_int(item.get("question_id")),
                            item_order=int(item["item_order"]),
                            stage=str(item["stage"]),
                        )
                        for item in variant["items"]
                    ),
                )
                for variant in loaded["variants"]
            ),
        )


def _task_from_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["scope_snapshot"] = _json_value(item.pop("scope_json"), {})
    item["exam_scope"] = _json_value(item.pop("exam_scope_json"), {})
    item["diagnosis_snapshot"] = _json_value(item.pop("diagnosis_snapshot_json"), {})
    item["generation_config"] = _json_value(item.pop("generation_config_json"), {})
    item["warnings"] = _json_value(item.pop("warnings_json"), [])
    return item


def _variant_payload(conn: Any, row: Any) -> dict[str, Any]:
    item = dict(row)
    variant_id = int(item["id"])
    item["grouping_reason"] = _json_value(item.pop("grouping_reason_json"), {})
    item["diagnosis_snapshot"] = _json_value(item.pop("diagnosis_snapshot_json"), {})
    item["shortages"] = _json_value(item.pop("shortages_json"), [])
    item["warnings"] = _json_value(item.pop("warnings_json"), [])
    item["students"] = [
        dict(student)
        for student in conn.execute(
            "SELECT * FROM variant_students WHERE variant_id = ? ORDER BY student_id",
            (variant_id,),
        ).fetchall()
    ]
    item["items"] = []
    for task_item in conn.execute(
        "SELECT * FROM training_task_items WHERE variant_id = ? ORDER BY item_order, id",
        (variant_id,),
    ).fetchall():
        payload = dict(task_item)
        payload["question_id"] = payload.get("bank_question_id")
        payload["question_fingerprint"] = payload.get("bank_question_fingerprint")
        payload["concept_snapshot"] = _json_value(payload.pop("concept_snapshot_json"), {})
        payload["recommendation_snapshot"] = _json_value(payload.pop("recommendation_snapshot_json"), {})
        payload["question_snapshot"] = _json_value(payload.pop("question_snapshot_json"), {})
        item["items"].append(payload)
    return item


def _question_snapshot(
    conn: Any,
    question_id: int | None,
    fallback: Mapping[str, Any],
) -> dict[str, Any]:
    if question_id is None:
        return dict(fallback.get("question_snapshot") or fallback)
    row = conn.execute(
        """
        SELECT q.*, p.title AS paper_title, p.source_file AS paper_source_file
        FROM questions q
        LEFT JOIN papers p ON p.id = q.paper_id
        WHERE q.id = ?
        """,
        (int(question_id),),
    ).fetchone()
    if row is None:
        return dict(fallback.get("question_snapshot") or fallback)
    snapshot = dict(row)
    snapshot["tags"] = [
        dict(tag)
        for tag in conn.execute(
            """
            SELECT tag_type, tag_value, confidence, source, model_name
            FROM question_tags
            WHERE question_id = ?
            ORDER BY id
            """,
            (int(question_id),),
        ).fetchall()
    ]
    return snapshot


def _student_index(diagnosis_snapshot: object) -> dict[str, dict[str, Any]]:
    if not isinstance(diagnosis_snapshot, Mapping):
        return {}
    return {
        str(item["student_id"]): dict(item)
        for item in diagnosis_snapshot.get("students", [])
        if isinstance(item, Mapping) and item.get("student_id") is not None
    }


def _new_task_code() -> str:
    return f"TRN-{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:8].upper()}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_value(value: object, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@contextmanager
def _read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


__all__ = [
    "TrainingTaskItemRecord",
    "TrainingTaskRecord",
    "TrainingTaskService",
    "TrainingVariantRecord",
]
