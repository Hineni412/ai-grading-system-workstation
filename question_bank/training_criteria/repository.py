from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from question_bank.database.schema import connect
from question_bank.training_criteria.analysis import (
    AnalysisConflictError,
    AnalysisProjection,
    GatewayBatchResponse,
    PlannedAnalysisBatch,
    ProjectionStatus,
    QuestionAnalysisInput,
)


class CombinedAnalysisRepository:
    """Persist projection state without storing question or image bodies."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def begin_operation(
        self,
        *,
        operation_id: str,
        fingerprint: str,
        questions: Sequence[QuestionAnalysisInput],
        requested_projection: AnalysisProjection,
    ) -> bool:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT input_fingerprint, requested_projection
                FROM question_analysis_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is not None:
                if (
                    str(row["input_fingerprint"]) != fingerprint
                    or str(row["requested_projection"])
                    != requested_projection
                ):
                    raise AnalysisConflictError(
                        "operation_id already belongs to different input"
                    )
                return False
            connection.execute(
                """
                INSERT INTO question_analysis_operations (
                    operation_id,
                    input_fingerprint,
                    contract_version,
                    requested_projection
                ) VALUES (?, ?, 'combined-v2', ?)
                """,
                (operation_id, fingerprint, requested_projection),
            )
            tag_status = (
                "pending"
                if requested_projection in {"both", "tag"}
                else "not_requested"
            )
            criteria_status = (
                "pending"
                if requested_projection in {"both", "training_criteria"}
                else "not_requested"
            )
            connection.executemany(
                """
                INSERT INTO question_analysis_items (
                    operation_id,
                    question_id,
                    source_content_hash,
                    tag_status,
                    criteria_status
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        operation_id,
                        item.question_id,
                        item.source_content_hash,
                        tag_status,
                        criteria_status,
                    )
                    for item in questions
                ),
            )
        return True

    def projection_status(
        self,
        operation_id: str,
        question_id: int,
        projection: str,
    ) -> ProjectionStatus:
        column = _status_column(projection)
        with connect(self.db_path) as connection:
            row = connection.execute(
                f"""
                SELECT {column} AS projection_status
                FROM question_analysis_items
                WHERE operation_id = ? AND question_id = ?
                """,
                (operation_id, int(question_id)),
            ).fetchone()
        if row is None:
            raise KeyError((operation_id, question_id))
        return str(row["projection_status"])  # type: ignore[return-value]

    def record_request_started(
        self,
        *,
        operation_id: str,
        request_id: str,
        projection: AnalysisProjection,
        batch: PlannedAnalysisBatch,
    ) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO question_analysis_requests (
                    request_id,
                    operation_id,
                    projection,
                    batch_hash,
                    question_ids_json,
                    status
                ) VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (
                    request_id,
                    operation_id,
                    projection,
                    batch.batch_hash,
                    json.dumps(batch.question_ids),
                ),
            )

    def record_request_finished(
        self,
        *,
        request_id: str,
        status: Literal["succeeded", "failed"],
        response: GatewayBatchResponse | None = None,
        error_category: str = "",
    ) -> None:
        usage = response.usage if response is not None else None
        with connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE question_analysis_requests
                SET status = ?,
                    model_name = ?,
                    prompt_tokens = ?,
                    completion_tokens = ?,
                    total_tokens = ?,
                    latency_ms = ?,
                    error_category = ?,
                    finished_at = datetime('now','localtime')
                WHERE request_id = ? AND status = 'running'
                """,
                (
                    status,
                    str(response.model_name if response else ""),
                    int(usage.prompt_tokens if usage else 0),
                    int(usage.completion_tokens if usage else 0),
                    int(usage.total_tokens if usage else 0),
                    int(response.latency_ms if response else 0),
                    str(error_category or ""),
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise AnalysisConflictError(
                    "analysis request is no longer running"
                )

    def save_projection(
        self,
        *,
        operation_id: str,
        question_id: int,
        projection: Literal["tag", "training_criteria"],
        status: Literal["succeeded", "failed", "cancelled"],
        payload: Mapping[str, Any] | None = None,
        error_category: str = "",
    ) -> None:
        status_column = _status_column(projection)
        payload_column = (
            "tag_payload_hash"
            if projection == "tag"
            else "criteria_payload_json"
        )
        error_column = (
            "tag_error_category"
            if projection == "tag"
            else "criteria_error_category"
        )
        serialized = (
            None
            if payload is None
            else json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        stored_payload = (
            None
            if serialized is None
            else (
                hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                if projection == "tag"
                else serialized
            )
        )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT {status_column} AS projection_status,
                       {payload_column} AS projection_payload
                FROM question_analysis_items
                WHERE operation_id = ? AND question_id = ?
                """,
                (operation_id, int(question_id)),
            ).fetchone()
            if row is None:
                raise KeyError((operation_id, question_id))
            current = str(row["projection_status"])
            if current == "succeeded":
                if (
                    status == "succeeded"
                    and row["projection_payload"] == stored_payload
                ):
                    return
                raise AnalysisConflictError(
                    "successful projection is immutable"
                )
            connection.execute(
                f"""
                UPDATE question_analysis_items
                SET {status_column} = ?,
                    {payload_column} = ?,
                    {error_column} = ?,
                    revision = revision + 1,
                    updated_at = datetime('now','localtime')
                WHERE operation_id = ? AND question_id = ?
                """,
                (
                    status,
                    stored_payload,
                    str(error_category or ""),
                    operation_id,
                    int(question_id),
                ),
            )
            self._refresh_operation_status(connection, operation_id)

    def recover_interrupted(self, operation_id: str) -> None:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                """
                SELECT status
                FROM question_analysis_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if operation is None:
                raise KeyError(operation_id)
            connection.execute(
                """
                UPDATE question_analysis_requests
                SET status = 'failed',
                    error_category = 'interrupted',
                    finished_at = datetime('now','localtime')
                WHERE operation_id = ? AND status = 'running'
                """,
                (operation_id,),
            )
            connection.execute(
                """
                UPDATE question_analysis_items
                SET tag_status = CASE
                        WHEN tag_status = 'pending' THEN 'failed'
                        ELSE tag_status
                    END,
                    tag_error_category = CASE
                        WHEN tag_status = 'pending' THEN 'interrupted'
                        ELSE tag_error_category
                    END,
                    criteria_status = CASE
                        WHEN criteria_status = 'pending' THEN 'failed'
                        ELSE criteria_status
                    END,
                    criteria_error_category = CASE
                        WHEN criteria_status = 'pending' THEN 'interrupted'
                        ELSE criteria_error_category
                    END,
                    revision = revision + CASE
                        WHEN tag_status = 'pending'
                          OR criteria_status = 'pending'
                        THEN 1 ELSE 0
                    END,
                    updated_at = datetime('now','localtime')
                WHERE operation_id = ?
                """,
                (operation_id,),
            )
            self._refresh_operation_status(connection, operation_id)

    def operation_summary(self, operation_id: str) -> Mapping[str, Any]:
        with connect(self.db_path) as connection:
            operation = connection.execute(
                """
                SELECT *
                FROM question_analysis_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if operation is None:
                raise KeyError(operation_id)
            items = connection.execute(
                """
                SELECT question_id, source_content_hash,
                       tag_status, criteria_status,
                       tag_error_category, criteria_error_category,
                       criteria_payload_json, revision
                FROM question_analysis_items
                WHERE operation_id = ?
                ORDER BY question_id
                """,
                (operation_id,),
            ).fetchall()
            request = connection.execute(
                """
                SELECT
                    COUNT(*) AS request_count,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(total_tokens) AS total_tokens
                FROM question_analysis_requests
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        return {
            "operation_id": str(operation["operation_id"]),
            "input_fingerprint": str(operation["input_fingerprint"]),
            "contract_version": str(operation["contract_version"]),
            "requested_projection": str(
                operation["requested_projection"]
            ),
            "status": str(operation["status"]),
            "request_count": int(request["request_count"] or 0),
            "usage": {
                "prompt_tokens": int(request["prompt_tokens"] or 0),
                "completion_tokens": int(
                    request["completion_tokens"] or 0
                ),
                "total_tokens": int(request["total_tokens"] or 0),
            },
            "items": [
                {
                    "question_id": int(row["question_id"]),
                    "source_content_hash": str(
                        row["source_content_hash"]
                    ),
                    "tag_status": str(row["tag_status"]),
                    "criteria_status": str(row["criteria_status"]),
                    "tag_error_category": str(
                        row["tag_error_category"] or ""
                    ),
                    "criteria_error_category": str(
                        row["criteria_error_category"] or ""
                    ),
                    "training_criteria": (
                        None
                        if row["criteria_payload_json"] is None
                        else json.loads(
                            str(row["criteria_payload_json"])
                        )
                    ),
                    "revision": int(row["revision"]),
                }
                for row in items
            ],
        }

    @staticmethod
    def _refresh_operation_status(
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT tag_status, criteria_status
            FROM question_analysis_items
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchall()
        active = [
            str(value)
            for row in rows
            for value in (row["tag_status"], row["criteria_status"])
            if str(value) != "not_requested"
        ]
        if any(value == "pending" for value in active):
            status = "running"
        elif active and all(value == "succeeded" for value in active):
            status = "succeeded"
        elif any(value == "succeeded" for value in active):
            status = "partial"
        elif active and all(value == "cancelled" for value in active):
            status = "cancelled"
        else:
            status = "failed"
        connection.execute(
            """
            UPDATE question_analysis_operations
            SET status = ?, updated_at = datetime('now','localtime')
            WHERE operation_id = ?
            """,
            (status, operation_id),
        )


def _status_column(projection: str) -> str:
    if projection == "tag":
        return "tag_status"
    if projection == "training_criteria":
        return "criteria_status"
    raise ValueError("projection is invalid")


__all__ = ["CombinedAnalysisRepository"]
