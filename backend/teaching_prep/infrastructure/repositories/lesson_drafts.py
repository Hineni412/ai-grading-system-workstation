from __future__ import annotations

import hashlib
import json
import sqlite3
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.domain.models import LessonDraftVersion
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class LessonDraftRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def find_generation(
        self,
        *,
        operation_id: str,
        request_hash: str,
    ) -> LessonDraftVersion | None:
        with self._database.connect() as connection:
            operation = connection.execute(
                """
                SELECT *
                FROM teaching_prep_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if operation is None:
                return None
            if str(operation["request_hash"]) != request_hash:
                raise TeachingPrepConflictError(
                    "operation ID was reused for different draft input"
                )
            if str(operation["status"]) == "succeeded":
                row = connection.execute(
                    """
                    SELECT *
                    FROM lesson_draft_versions
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        "successful draft operation has no saved result"
                    )
                return _draft(row)
            raise TeachingPrepConflictError(
                "operation has already been consumed"
            )

    def begin_generation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        resource_pack_id: str,
        source_kind: str,
    ) -> None:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM teaching_prep_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "operation ID was reused for different draft input"
                    )
                raise TeachingPrepConflictError(
                    "operation has already been consumed"
                )
            connection.execute(
                """
                INSERT INTO teaching_prep_operations (
                    operation_id,
                    operation_type,
                    idempotency_key,
                    request_hash,
                    target_kind,
                    target_id,
                    status
                )
                VALUES (?, ?, ?, ?, 'resource_pack', ?, 'running')
                """,
                (
                    operation_id,
                    f"lesson_draft_{source_kind}",
                    operation_id,
                    request_hash,
                    resource_pack_id,
                ),
            )

    def finish_generation(
        self,
        *,
        operation_id: str,
        request_token: str,
        request_hash: str,
        resource_pack_id: str,
        source_kind: str,
        model_label: str | None,
        payload: dict[str, object],
        capacity: dict[str, object],
    ) -> LessonDraftVersion:
        return self._create(
            request_token=request_token,
            request_hash=request_hash,
            resource_pack_id=resource_pack_id,
            based_on_draft_id=None,
            operation_id=operation_id,
            source_kind=source_kind,
            model_label=model_label,
            status="draft",
            payload=payload,
            capacity=capacity,
            finish_operation=True,
        )[0]

    def fail_generation(self, operation_id: str, error_code: str) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'failed',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (error_code, operation_id),
            )

    def cancel_generation(self, operation_id: str) -> bool:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT operation_type, status
                FROM teaching_prep_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None or not str(row["operation_type"]).startswith(
                "lesson_draft_"
            ):
                raise TeachingPrepNotFoundError(
                    "lesson draft generation was not found"
                )
            current = str(row["status"])
            if current == "cancelled":
                return False
            if current != "running":
                raise TeachingPrepConflictError(
                    "lesson draft generation can no longer be cancelled"
                )
            cursor = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'cancelled',
                    error_code = 'teacher_cancelled',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (operation_id,),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepConflictError(
                    "lesson draft generation state changed"
                )
            return True

    def create_revision(
        self,
        *,
        request_token: str,
        request_hash: str,
        based_on_draft_id: str,
        payload: dict[str, object],
        capacity: dict[str, object],
        status: str,
    ) -> tuple[LessonDraftVersion, bool]:
        current = self.get(based_on_draft_id)
        return self._create(
            request_token=request_token,
            request_hash=request_hash,
            resource_pack_id=current.resource_pack_id,
            based_on_draft_id=current.id,
            operation_id=None,
            source_kind="teacher",
            model_label=None,
            status=status,
            payload=payload,
            capacity=capacity,
            finish_operation=False,
        )

    def _create(
        self,
        *,
        request_token: str,
        request_hash: str,
        resource_pack_id: str,
        based_on_draft_id: str | None,
        operation_id: str | None,
        source_kind: str,
        model_label: str | None,
        status: str,
        payload: dict[str, object],
        capacity: dict[str, object],
        finish_operation: bool,
    ) -> tuple[LessonDraftVersion, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM lesson_draft_versions
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for different draft data"
                    )
                return _draft(existing), False
            version_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM lesson_draft_versions
                    WHERE resource_pack_id = ?
                    """,
                    (resource_pack_id,),
                ).fetchone()[0]
            )
            draft_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO lesson_draft_versions (
                    id,
                    request_token,
                    request_hash,
                    resource_pack_id,
                    version_number,
                    based_on_draft_id,
                    operation_id,
                    source_kind,
                    model_label,
                    status,
                    payload_json,
                    capacity_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    request_token,
                    request_hash,
                    resource_pack_id,
                    version_number,
                    based_on_draft_id,
                    operation_id,
                    source_kind,
                    model_label,
                    status,
                    _canonical_json(payload),
                    _canonical_json(capacity),
                ),
            )
            if finish_operation:
                cursor = connection.execute(
                    """
                    UPDATE teaching_prep_operations
                    SET status = 'succeeded',
                        error_code = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE operation_id = ? AND status = 'running'
                    """,
                    (operation_id,),
                )
                if cursor.rowcount != 1:
                    raise TeachingPrepConflictError(
                        "draft operation is no longer running"
                    )
            row = connection.execute(
                "SELECT * FROM lesson_draft_versions WHERE id = ?",
                (draft_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("lesson draft could not be loaded")
        return _draft(row), True

    def get(self, draft_id: str) -> LessonDraftVersion:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM lesson_draft_versions WHERE id = ?",
                (draft_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "lesson draft version was not found"
            )
        return _draft(row)

    def list_for_pack(
        self,
        resource_pack_id: str,
    ) -> tuple[LessonDraftVersion, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM resource_pack_versions WHERE id = ?",
                (resource_pack_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError(
                    "resource pack version was not found"
                )
            rows = connection.execute(
                """
                SELECT *
                FROM lesson_draft_versions
                WHERE resource_pack_id = ?
                ORDER BY version_number DESC
                """,
                (resource_pack_id,),
            ).fetchall()
        return tuple(_draft(row) for row in rows)


def request_hash(value: object) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _draft(row: sqlite3.Row) -> LessonDraftVersion:
    payload = _json_object(str(row["payload_json"]))
    capacity = _json_object(str(row["capacity_json"]))
    return LessonDraftVersion(
        id=str(row["id"]),
        resource_pack_id=str(row["resource_pack_id"]),
        version_number=int(row["version_number"]),
        based_on_draft_id=(
            str(row["based_on_draft_id"])
            if row["based_on_draft_id"] is not None
            else None
        ),
        operation_id=(
            str(row["operation_id"])
            if row["operation_id"] is not None
            else None
        ),
        source_kind=str(row["source_kind"]),
        model_label=(
            str(row["model_label"])
            if row["model_label"] is not None
            else None
        ),
        status=str(row["status"]),
        payload=payload,
        capacity=capacity,
        created_at=str(row["created_at"]),
    )


def _json_object(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("stored lesson draft JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stored lesson draft payload is invalid")
    return payload


__all__ = ["LessonDraftRepository", "request_hash"]
