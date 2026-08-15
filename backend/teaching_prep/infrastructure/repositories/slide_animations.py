from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepStateError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import SlideAnimationRun
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class SlideAnimationRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def billed_count(self, lesson_node_id: str) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM slide_animation_runs
                WHERE lesson_node_id = ?
                  AND (status = 'running' OR model_call_count = 1)
                """,
                (lesson_node_id,),
            ).fetchone()
        return int(row["total"] if row is not None else 0)

    def list_for_lesson(self, lesson_node_id: str) -> tuple[SlideAnimationRun, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM slide_animation_runs
                WHERE lesson_node_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (lesson_node_id,),
            ).fetchall()
        return tuple(_run(row) for row in rows)

    def get(self, run_id: str) -> SlideAnimationRun:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("slide animation run was not found")
        return _run(row)

    def begin(
        self,
        *,
        lesson_node_id: str,
        material_version_id: str,
        material_link_id: str,
        operation_id: str,
        request_hash: str,
        page_indexes: Sequence[int],
        billed_limit: int,
    ) -> tuple[SlideAnimationRun, bool]:
        pages_json = _json(list(page_indexes))
        with self._database.connect(immediate=True) as connection:
            if connection.execute(
                "SELECT 1 FROM lesson_nodes WHERE id = ?",
                (lesson_node_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("lesson node was not found")
            existing = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "operation ID was reused for different animation input"
                    )
                return _run(existing), False
            semantic = connection.execute(
                """
                SELECT * FROM slide_animation_runs
                WHERE request_hash = ?
                  AND status IN ('running', 'succeeded', 'accepted')
                ORDER BY created_at DESC LIMIT 1
                """,
                (request_hash,),
            ).fetchone()
            if semantic is not None:
                return _run(semantic), False
            billed = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM slide_animation_runs
                WHERE lesson_node_id = ?
                  AND (status = 'running' OR model_call_count = 1)
                """,
                (lesson_node_id,),
            ).fetchone()
            if int(billed["total"]) >= billed_limit:
                raise TeachingPrepValidationError(
                    "this lesson already used the billed animation limit"
                )
            run_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO slide_animation_runs (
                    id, lesson_node_id, material_version_id, material_link_id,
                    operation_id, request_hash, page_indexes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    lesson_node_id,
                    material_version_id,
                    material_link_id,
                    operation_id,
                    request_hash,
                    pages_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("slide animation run was not created")
        return _run(row), True

    def mark_call_started(self, run_id: str) -> None:
        with self._database.connect(immediate=True) as connection:
            updated = connection.execute(
                """
                UPDATE slide_animation_runs
                SET model_call_count = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'running' AND model_call_count = 0
                """,
                (run_id,),
            ).rowcount
            if updated != 1:
                raise TeachingPrepStateError(
                    "slide animation call is no longer available"
                )

    def finish(
        self,
        *,
        run_id: str,
        storyboard: Mapping[str, object],
        html_relpath: str,
        html_sha256: str,
    ) -> SlideAnimationRun | None:
        with self._database.connect(immediate=True) as connection:
            updated = connection.execute(
                """
                UPDATE slide_animation_runs
                SET status = 'succeeded',
                    storyboard_json = ?,
                    html_relpath = ?,
                    html_sha256 = ?,
                    error_code = NULL,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'running'
                """,
                (_json(storyboard), html_relpath, html_sha256, run_id),
            ).rowcount
            if updated != 1:
                return None
            row = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("slide animation run was not loaded")
        return _run(row)

    def fail(self, run_id: str, error_code: str) -> None:
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE slide_animation_runs
                SET status = 'failed', error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'running'
                """,
                (error_code, run_id),
            )

    def cancel(self, run_id: str) -> SlideAnimationRun:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError("slide animation run was not found")
            if str(row["status"]) == "running":
                connection.execute(
                    """
                    UPDATE slide_animation_runs
                    SET status = 'cancelled',
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND status = 'running'
                    """,
                    (run_id,),
                )
            saved = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if saved is None:
            raise RuntimeError("slide animation run was not loaded")
        return _run(saved)

    def accept(self, run_id: str, *, expected_revision: int) -> SlideAnimationRun:
        return self._decide(
            run_id,
            expected_revision=expected_revision,
            status="accepted",
            teacher_decision="accepted",
            allowed_status="succeeded",
        )

    def discard(self, run_id: str, *, expected_revision: int) -> SlideAnimationRun:
        return self._decide(
            run_id,
            expected_revision=expected_revision,
            status="discarded",
            teacher_decision="discarded",
            allowed_status="succeeded",
        )

    def mark_interrupted(self) -> int:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE slide_animation_runs
                SET status = 'result_unknown',
                    error_code = 'application_restarted',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status = 'running'
                """
            )
            return int(cursor.rowcount)

    def _decide(
        self,
        run_id: str,
        *,
        expected_revision: int,
        status: str,
        teacher_decision: str,
        allowed_status: str,
    ) -> SlideAnimationRun:
        if int(expected_revision) <= 0:
            raise TeachingPrepValidationError("expected_revision must be positive")
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError("slide animation run was not found")
            if int(row["revision"]) != int(expected_revision):
                raise TeachingPrepConflictError(
                    "slide animation run changed; refresh and try again"
                )
            if str(row["status"]) != allowed_status:
                raise TeachingPrepStateError(
                    "slide animation run is not waiting for a teacher decision"
                )
            connection.execute(
                """
                UPDATE slide_animation_runs
                SET status = ?,
                    teacher_decision = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ? AND status = ?
                """,
                (status, teacher_decision, run_id, int(expected_revision), allowed_status),
            )
            saved = connection.execute(
                "SELECT * FROM slide_animation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if saved is None:
            raise RuntimeError("slide animation run was not loaded")
        return _run(saved)


def _run(row) -> SlideAnimationRun:
    pages = json.loads(str(row["page_indexes_json"]))
    storyboard_raw = row["storyboard_json"]
    storyboard = json.loads(str(storyboard_raw)) if storyboard_raw else None
    return SlideAnimationRun(
        id=str(row["id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        material_version_id=str(row["material_version_id"]),
        material_link_id=str(row["material_link_id"]),
        operation_id=str(row["operation_id"]),
        request_hash=str(row["request_hash"]),
        page_indexes=tuple(int(item) for item in pages),
        storyboard=dict(storyboard) if isinstance(storyboard, Mapping) else None,
        html_relpath=(
            str(row["html_relpath"]) if row["html_relpath"] is not None else None
        ),
        html_sha256=(
            str(row["html_sha256"]) if row["html_sha256"] is not None else None
        ),
        status=str(row["status"]),
        teacher_decision=str(row["teacher_decision"]),
        error_code=(
            str(row["error_code"]) if row["error_code"] is not None else None
        ),
        model_call_count=int(row["model_call_count"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=(
            str(row["finished_at"]) if row["finished_at"] is not None else None
        ),
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["SlideAnimationRepository"]
