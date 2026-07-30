from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.domain.models import SlidePlanVersion
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class SlidePlanRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def find_idempotent(
        self,
        *,
        request_token: str,
        request_hash: str,
    ) -> SlidePlanVersion | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM slide_plan_versions
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
        if row is None:
            return None
        if str(row["request_hash"]) != request_hash:
            raise TeachingPrepConflictError(
                "request token was reused for different slide plan data"
            )
        return _plan(row)

    def create(
        self,
        *,
        request_token: str,
        request_hash: str,
        lesson_draft_id: str,
        resource_pack_id: str,
        based_on_plan_id: str | None,
        source_ppt_state_sha256: str,
        status: str,
        payload: dict[str, object],
    ) -> tuple[SlidePlanVersion, bool]:
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM slide_plan_versions
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for different slide plan data"
                    )
                return _plan(existing), False
            version_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM slide_plan_versions
                    WHERE lesson_draft_id = ?
                    """,
                    (lesson_draft_id,),
                ).fetchone()[0]
            )
            plan_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO slide_plan_versions (
                    id,
                    request_token,
                    request_hash,
                    lesson_draft_id,
                    resource_pack_id,
                    version_number,
                    based_on_plan_id,
                    source_ppt_state_sha256,
                    status,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    request_token,
                    request_hash,
                    lesson_draft_id,
                    resource_pack_id,
                    version_number,
                    based_on_plan_id,
                    source_ppt_state_sha256,
                    status,
                    _canonical_json(payload),
                ),
            )
            row = connection.execute(
                "SELECT * FROM slide_plan_versions WHERE id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("slide plan could not be loaded")
        return _plan(row), True

    def get(self, plan_id: str) -> SlidePlanVersion:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM slide_plan_versions WHERE id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "slide plan version was not found"
            )
        return _plan(row)

    def list_for_draft(
        self,
        lesson_draft_id: str,
    ) -> tuple[SlidePlanVersion, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM lesson_draft_versions WHERE id = ?",
                (lesson_draft_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError(
                    "lesson draft version was not found"
                )
            rows = connection.execute(
                """
                SELECT *
                FROM slide_plan_versions
                WHERE lesson_draft_id = ?
                ORDER BY version_number DESC
                """,
                (lesson_draft_id,),
            ).fetchall()
        return tuple(_plan(row) for row in rows)


def _plan(row: sqlite3.Row) -> SlidePlanVersion:
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError as exc:
        raise RuntimeError("stored slide plan JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stored slide plan payload is invalid")
    return SlidePlanVersion(
        id=str(row["id"]),
        lesson_draft_id=str(row["lesson_draft_id"]),
        resource_pack_id=str(row["resource_pack_id"]),
        version_number=int(row["version_number"]),
        based_on_plan_id=(
            str(row["based_on_plan_id"])
            if row["based_on_plan_id"] is not None
            else None
        ),
        source_ppt_state_sha256=str(row["source_ppt_state_sha256"]),
        status=str(row["status"]),
        payload=payload,
        created_at=str(row["created_at"]),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["SlidePlanRepository"]
