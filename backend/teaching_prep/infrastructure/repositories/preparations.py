from __future__ import annotations

import hashlib
import json
import sqlite3
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.domain.models import LessonPreparation
from backend.teaching_prep.domain.states import LessonPreparationState
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class LessonPreparationRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def create(
        self,
        *,
        request_token: str,
        title: str,
        class_name: str | None,
    ) -> tuple[LessonPreparation, bool]:
        request_hash = _request_hash(
            {"title": title, "class_name": class_name}
        )
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM lesson_preparations
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for different preparation data"
                    )
                return _preparation(existing), False
            preparation_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO lesson_preparations (
                    id,
                    request_token,
                    request_hash,
                    title,
                    class_name,
                    state
                )
                VALUES (?, ?, ?, ?, ?, 'selecting_sources')
                """,
                (
                    preparation_id,
                    request_token,
                    request_hash,
                    title,
                    class_name,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM lesson_preparations
                WHERE id = ?
                """,
                (preparation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("created preparation could not be loaded")
        return _preparation(row), True

    def get(self, preparation_id: str) -> LessonPreparation:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM lesson_preparations
                WHERE id = ?
                """,
                (preparation_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("preparation was not found")
        return _preparation(row)

    def list(self) -> tuple[LessonPreparation, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM lesson_preparations
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        return tuple(_preparation(row) for row in rows)

    def update(
        self,
        preparation_id: str,
        *,
        expected_revision: int,
        title: str,
        class_name: str | None,
        state: LessonPreparationState,
    ) -> LessonPreparation:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE lesson_preparations
                SET title = ?,
                    class_name = ?,
                    state = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    title,
                    class_name,
                    state.value,
                    preparation_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                current = connection.execute(
                    """
                    SELECT revision
                    FROM lesson_preparations
                    WHERE id = ?
                    """,
                    (preparation_id,),
                ).fetchone()
                if current is None:
                    raise TeachingPrepNotFoundError(
                        "preparation was not found"
                    )
                raise TeachingPrepConflictError(
                    f"preparation revision changed to {int(current['revision'])}"
                )
            row = connection.execute(
                """
                SELECT *
                FROM lesson_preparations
                WHERE id = ?
                """,
                (preparation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("updated preparation could not be loaded")
        return _preparation(row)


def _request_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preparation(row: sqlite3.Row) -> LessonPreparation:
    return LessonPreparation(
        id=str(row["id"]),
        title=str(row["title"]),
        class_name=(
            str(row["class_name"]) if row["class_name"] is not None else None
        ),
        state=LessonPreparationState(str(row["state"])),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
