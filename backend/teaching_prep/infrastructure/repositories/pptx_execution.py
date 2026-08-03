from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.domain.models import (
    LessonGenerationPerformance,
    PptxExecutionRun,
    PptxVersion,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


class PptxExecutionRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def begin(
        self,
        *,
        operation_id: str,
        request_hash: str,
        slide_plan_id: str,
        source_material_version_id: str,
        source_sha256: str,
        expected_slide_count: int,
    ) -> tuple[PptxExecutionRun, bool]:
        with self._database.connect(immediate=True) as connection:
            existing_operation = connection.execute(
                """
                SELECT request_hash
                FROM teaching_prep_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if existing_operation is not None:
                if str(existing_operation["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "operation ID was reused for different execution input"
                    )
                row = self._run_query(
                    connection,
                    "run.operation_id = ?",
                    (operation_id,),
                )
                if row is None:
                    raise RuntimeError(
                        "execution operation has no execution record"
                    )
                return _run(row), False
            plan_run = self._run_query(
                connection,
                "run.slide_plan_id = ?",
                (slide_plan_id,),
            )
            if plan_run is not None:
                raise TeachingPrepConflictError(
                    "slide plan already has an execution operation"
                )
            run_id = uuid4().hex
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
                VALUES (
                    ?,
                    'pptx_copy_execution',
                    ?,
                    ?,
                    'slide_plan',
                    ?,
                    'running'
                )
                """,
                (
                    operation_id,
                    operation_id,
                    request_hash,
                    slide_plan_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO pptx_execution_runs (
                    id,
                    operation_id,
                    request_hash,
                    slide_plan_id,
                    source_material_version_id,
                    source_sha256,
                    staging_name,
                    expected_slide_count,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
                """,
                (
                    run_id,
                    operation_id,
                    request_hash,
                    slide_plan_id,
                    source_material_version_id,
                    source_sha256,
                    run_id,
                    expected_slide_count,
                ),
            )
            row = self._run_query(connection, "run.id = ?", (run_id,))
        if row is None:
            raise RuntimeError("execution record could not be loaded")
        return _run(row), True

    def get(self, run_id: str) -> PptxExecutionRun:
        with self._database.connect() as connection:
            row = self._run_query(connection, "run.id = ?", (run_id,))
        if row is None:
            raise TeachingPrepNotFoundError(
                "PPTX execution record was not found"
            )
        return _run(row)

    def get_by_operation(self, operation_id: str) -> PptxExecutionRun:
        with self._database.connect() as connection:
            row = self._run_query(
                connection,
                "run.operation_id = ?",
                (operation_id,),
            )
        if row is None:
            raise TeachingPrepNotFoundError(
                "PPTX execution record was not found"
            )
        return _run(row)

    def list_for_plan(self, slide_plan_id: str) -> tuple[PptxExecutionRun, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT run.*
                FROM pptx_execution_runs AS run
                WHERE run.slide_plan_id = ?
                ORDER BY run.created_at DESC, run.id DESC
                """,
                (slide_plan_id,),
            ).fetchall()
        return tuple(_run(row) for row in rows)

    def performance(
        self,
        run_id: str,
        *,
        budget_ms: int = 300_000,
    ) -> LessonGenerationPerformance:
        with self._database.connect() as connection:
            run = connection.execute(
                """
                SELECT
                    execution.*,
                    operation.created_at AS operation_created_at,
                    operation.finished_at AS operation_finished_at
                FROM pptx_execution_runs AS execution
                JOIN teaching_prep_operations AS operation
                  ON operation.operation_id = execution.operation_id
                WHERE execution.id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise TeachingPrepNotFoundError(
                    "PPTX execution record was not found"
                )
            lineage = connection.execute(
                """
                WITH RECURSIVE draft_lineage(
                    id,
                    based_on_draft_id,
                    operation_id,
                    source_kind,
                    depth
                ) AS (
                    SELECT
                        draft.id,
                        draft.based_on_draft_id,
                        draft.operation_id,
                        draft.source_kind,
                        0
                    FROM slide_plan_versions AS plan
                    JOIN lesson_draft_versions AS draft
                      ON draft.id = plan.lesson_draft_id
                    WHERE plan.id = ?
                    UNION ALL
                    SELECT
                        parent.id,
                        parent.based_on_draft_id,
                        parent.operation_id,
                        parent.source_kind,
                        lineage.depth + 1
                    FROM lesson_draft_versions AS parent
                    JOIN draft_lineage AS lineage
                      ON parent.id = lineage.based_on_draft_id
                )
                SELECT
                    lineage.*,
                    operation.created_at AS operation_created_at,
                    operation.finished_at AS operation_finished_at
                FROM draft_lineage AS lineage
                LEFT JOIN teaching_prep_operations AS operation
                  ON operation.operation_id = lineage.operation_id
                ORDER BY lineage.depth
                """,
                (str(run["slide_plan_id"]),),
            ).fetchall()
        draft_row = next(
            (row for row in lineage if row["operation_id"] is not None),
            None,
        )
        now = datetime.now(UTC)
        draft_elapsed = (
            _elapsed_ms(
                str(draft_row["operation_created_at"]),
                (
                    str(draft_row["operation_finished_at"])
                    if draft_row["operation_finished_at"] is not None
                    else None
                ),
                now=now,
            )
            if draft_row is not None
            else 0
        )
        wps_elapsed = _elapsed_ms(
            str(run["operation_created_at"]),
            (
                str(run["operation_finished_at"])
                if run["operation_finished_at"] is not None
                else None
            ),
            now=now,
        )
        total = draft_elapsed + wps_elapsed
        status = str(run["status"])
        terminal = status in {
            "published",
            "failed",
            "cancelled",
            "interrupted",
        }
        return LessonGenerationPerformance(
            execution_run_id=str(run["id"]),
            slide_plan_id=str(run["slide_plan_id"]),
            status=status,
            budget_ms=budget_ms,
            total_machine_elapsed_ms=total,
            draft_elapsed_ms=draft_elapsed,
            wps_elapsed_ms=wps_elapsed,
            model_call_count=(
                1
                if draft_row is not None
                and str(draft_row["source_kind"]) == "model"
                else 0
            ),
            wps_execution_count=int(run["wps_invocation_count"]),
            technical_retry_count=0,
            budget_status=(
                "exceeded"
                if total > budget_ms
                else "within"
                if terminal
                else "running"
            ),
            within_budget=(total <= budget_ms if terminal else None),
            human_review_wait_excluded=True,
        )

    def mark_wps_started(self, run_id: str) -> None:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE pptx_execution_runs
                SET wps_started_at = strftime(
                        '%Y-%m-%dT%H:%M:%fZ',
                        'now'
                    ),
                    wps_invocation_count = 1,
                    phase = 'executing',
                    phase_started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    updated_at = strftime(
                        '%Y-%m-%dT%H:%M:%fZ',
                        'now'
                    )
                WHERE id = ?
                  AND status = 'running'
                  AND wps_invocation_count = 0
                """,
                (run_id,),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepConflictError(
                    "WPS execution cannot start more than once"
                )

    def staging_name(self, run_id: str) -> str:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT staging_name FROM pptx_execution_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "PPTX execution record was not found"
            )
        return str(row["staging_name"])

    def set_verifying(
        self,
        run_id: str,
        execution_report: dict[str, object],
    ) -> None:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'verifying',
                    phase = 'verifying',
                    phase_started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    execution_report_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'running'
                """,
                (_json(execution_report), run_id),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepConflictError(
                    "execution is no longer running"
                )

    def begin_publish(
        self,
        *,
        run_id: str,
        lesson_node_id: str,
        slide_plan_id: str,
        slide_count: int,
        verification_report: dict[str, object],
    ) -> tuple[PptxVersion, str]:
        with self._database.connect(immediate=True) as connection:
            run = connection.execute(
                "SELECT * FROM pptx_execution_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise TeachingPrepNotFoundError(
                    "PPTX execution record was not found"
                )
            if str(run["status"]) != "verifying":
                raise TeachingPrepConflictError(
                    "execution is no longer awaiting publication"
                )
            version_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM pptx_versions
                    WHERE lesson_node_id = ?
                    """,
                    (lesson_node_id,),
                ).fetchone()[0]
            )
            version_id = uuid4().hex
            filename = (
                f"lesson-{lesson_node_id[:8]}-v{version_number:04d}-"
                f"{version_id[:8]}.pptx"
            )
            relative = (
                f"outputs/{lesson_node_id}/{filename}"
            )
            connection.execute(
                """
                INSERT INTO pptx_versions (
                    id,
                    slide_plan_id,
                    lesson_node_id,
                    execution_run_id,
                    version_number,
                    status,
                    output_relpath,
                    output_filename,
                    slide_count,
                    verification_report_json
                )
                VALUES (?, ?, ?, ?, ?, 'publishing', ?, ?, ?, ?)
                """,
                (
                    version_id,
                    slide_plan_id,
                    lesson_node_id,
                    run_id,
                    version_number,
                    relative,
                    filename,
                    slide_count,
                    _json(verification_report),
                ),
            )
            connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'publishing',
                    phase = 'publishing',
                    phase_started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    verification_report_json = ?,
                    published_version_id = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (_json(verification_report), version_id, run_id),
            )
            row = connection.execute(
                "SELECT * FROM pptx_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("PPTX version reservation could not be loaded")
        return _version(row), relative

    def finish_publish(
        self,
        *,
        run_id: str,
        version_id: str,
        output_sha256: str,
        deadline_at: str,
    ) -> PptxVersion:
        with self._database.connect(immediate=True) as connection:
            version_cursor = connection.execute(
                """
                UPDATE pptx_versions
                SET status = 'published',
                    output_sha256 = ?,
                    published_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status = 'publishing'
                  AND julianday(?) > julianday('now')
                """,
                (output_sha256, version_id, deadline_at),
            )
            run_cursor = connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'published',
                    phase = 'done',
                    error_code = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status IN ('publishing', 'interrupted')
                  AND published_version_id = ?
                  AND julianday(?) > julianday('now')
                """,
                (run_id, version_id, deadline_at),
            )
            operation_cursor = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'succeeded',
                    error_code = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = (
                    SELECT operation_id
                    FROM pptx_execution_runs
                    WHERE id = ?
                )
                  AND status IN ('running', 'interrupted')
                  AND julianday(?) > julianday('now')
                """,
                (run_id, deadline_at),
            )
            if (
                version_cursor.rowcount != 1
                or run_cursor.rowcount != 1
                or operation_cursor.rowcount != 1
            ):
                if _deadline_has_elapsed(connection, deadline_at):
                    raise TimeoutError("lesson generation budget exceeded")
                raise TeachingPrepConflictError(
                    "PPTX publication state changed"
                )
            version = connection.execute(
                "SELECT lesson_node_id FROM pptx_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
            if version is None:
                raise RuntimeError("published PPTX lesson was not found")
            lesson_id = str(version["lesson_node_id"])
            current = connection.execute(
                """
                SELECT pptx_version_id, revision
                FROM lesson_current_pptx_versions
                WHERE lesson_node_id = ?
                """,
                (lesson_id,),
            ).fetchone()
            previous = (
                str(current["pptx_version_id"]) if current is not None else None
            )
            revision = int(current["revision"]) + 1 if current is not None else 1
            connection.execute(
                """
                INSERT INTO lesson_current_pptx_versions (
                    lesson_node_id, pptx_version_id, revision
                ) VALUES (?, ?, ?)
                ON CONFLICT(lesson_node_id) DO UPDATE SET
                    pptx_version_id = excluded.pptx_version_id,
                    revision = excluded.revision,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (lesson_id, version_id, revision),
            )
            connection.execute(
                """
                INSERT INTO pptx_version_activations (
                    id, lesson_node_id, pptx_version_id,
                    previous_pptx_version_id, resulting_revision
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (uuid4().hex, lesson_id, version_id, previous, revision),
            )
            row = connection.execute(
                "SELECT * FROM pptx_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("published PPTX version could not be loaded")
        return _version(row)

    def fail(self, run_id: str, error_code: str) -> None:
        with self._database.connect(immediate=True) as connection:
            run = connection.execute(
                "SELECT operation_id, published_version_id FROM pptx_execution_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                return
            connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'failed',
                    phase = 'done',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status IN (
                      'running',
                      'verifying',
                      'publishing',
                      'interrupted'
                  )
                """,
                (error_code, run_id),
            )
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'failed',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ?
                  AND status IN ('running', 'interrupted')
                """,
                (error_code, str(run["operation_id"])),
            )
            if run["published_version_id"] is not None:
                connection.execute(
                    """
                    UPDATE pptx_versions
                    SET status = 'failed'
                    WHERE id = ? AND status = 'publishing'
                    """,
                    (str(run["published_version_id"]),),
                )

    def revoke_published(
        self,
        *,
        run_id: str,
        version_id: str,
        error_code: str,
    ) -> None:
        """Make a just-published version unavailable after a deadline breach.

        This narrow recovery path is only used when the final budget check
        discovers that publication crossed the hard generation deadline.
        """
        with self._database.connect(immediate=True) as connection:
            version_cursor = connection.execute(
                """
                UPDATE pptx_versions
                SET status = 'failed'
                WHERE id = ?
                  AND execution_run_id = ?
                  AND status = 'published'
                """,
                (version_id, run_id),
            )
            run_cursor = connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'failed',
                    phase = 'done',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status = 'published'
                  AND published_version_id = ?
                """,
                (error_code, run_id, version_id),
            )
            operation_cursor = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'failed',
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = (
                    SELECT operation_id
                    FROM pptx_execution_runs
                    WHERE id = ?
                )
                  AND status = 'succeeded'
                """,
                (error_code, run_id),
            )
            if (
                version_cursor.rowcount != 1
                or run_cursor.rowcount != 1
                or operation_cursor.rowcount != 1
            ):
                raise TeachingPrepConflictError(
                    "published PPTX state changed before deadline recovery"
                )
            activation = connection.execute(
                """
                SELECT * FROM pptx_version_activations
                WHERE pptx_version_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (version_id,),
            ).fetchone()
            if activation is not None:
                lesson_id = str(activation["lesson_node_id"])
                previous = activation["previous_pptx_version_id"]
                if previous is None:
                    connection.execute(
                        """
                        DELETE FROM lesson_current_pptx_versions
                        WHERE lesson_node_id = ? AND pptx_version_id = ?
                        """,
                        (lesson_id, version_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE lesson_current_pptx_versions
                        SET pptx_version_id = ?, revision = revision + 1,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE lesson_node_id = ? AND pptx_version_id = ?
                        """,
                        (str(previous), lesson_id, version_id),
                    )

    def cancel(self, run_id: str) -> PptxExecutionRun:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT operation_id, status FROM pptx_execution_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "PPTX execution record was not found"
                )
            if str(row["status"]) not in {"running", "verifying"}:
                raise TeachingPrepConflictError(
                    "execution can no longer be cancelled"
                )
            connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'cancelled',
                    phase = 'done',
                    cancel_requested = 1,
                    error_code = 'teacher_cancelled',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (run_id,),
            )
            connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'cancelled',
                    error_code = 'teacher_cancelled',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE operation_id = ? AND status = 'running'
                """,
                (str(row["operation_id"]),),
            )
            updated = self._run_query(connection, "run.id = ?", (run_id,))
        if updated is None:
            raise RuntimeError("cancelled execution could not be loaded")
        return _run(updated)

    def mark_interrupted(self) -> int:
        with self._database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'interrupted',
                    phase = 'done',
                    error_code = 'application_restarted',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status IN ('running', 'verifying', 'publishing')
                """
            )
            return int(cursor.rowcount)

    def get_version(self, version_id: str) -> PptxVersion:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pptx_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        if row is None or str(row["status"]) != "published":
            raise TeachingPrepNotFoundError(
                "published PPTX version was not found"
            )
        return _version(row)

    def list_versions_for_lesson(
        self,
        lesson_node_id: str,
    ) -> tuple[tuple[PptxVersion, bool, int | None], ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM lesson_nodes WHERE id = ?",
                (lesson_node_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError("lesson node was not found")
            current = connection.execute(
                """
                SELECT pptx_version_id, revision
                FROM lesson_current_pptx_versions
                WHERE lesson_node_id = ?
                """,
                (lesson_node_id,),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT * FROM pptx_versions
                WHERE lesson_node_id = ? AND status = 'published'
                ORDER BY version_number DESC
                """,
                (lesson_node_id,),
            ).fetchall()
        current_id = str(current["pptx_version_id"]) if current else None
        revision = int(current["revision"]) if current else None
        return tuple(
            (_version(row), str(row["id"]) == current_id, revision)
            for row in rows
        )

    def activate_version(
        self,
        version_id: str,
        *,
        expected_revision: int | None,
    ) -> tuple[PptxVersion, int, bool]:
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM pptx_versions
                WHERE id = ? AND status = 'published'
                """,
                (version_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "published PPTX version was not found"
                )
            lesson_id = str(row["lesson_node_id"])
            current = connection.execute(
                """
                SELECT pptx_version_id, revision
                FROM lesson_current_pptx_versions
                WHERE lesson_node_id = ?
                """,
                (lesson_id,),
            ).fetchone()
            if current is None:
                if expected_revision is not None:
                    raise TeachingPrepConflictError(
                        "current PPTX selection changed; refresh before restoring"
                    )
                previous = None
                revision = 1
                changed = True
            else:
                current_revision = int(current["revision"])
                if expected_revision != current_revision:
                    raise TeachingPrepConflictError(
                        "current PPTX selection changed; refresh before restoring"
                    )
                previous = str(current["pptx_version_id"])
                if previous == version_id:
                    return _version(row), current_revision, False
                revision = current_revision + 1
                changed = True
            connection.execute(
                """
                INSERT INTO lesson_current_pptx_versions (
                    lesson_node_id, pptx_version_id, revision
                ) VALUES (?, ?, ?)
                ON CONFLICT(lesson_node_id) DO UPDATE SET
                    pptx_version_id = excluded.pptx_version_id,
                    revision = excluded.revision,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (lesson_id, version_id, revision),
            )
            connection.execute(
                """
                INSERT INTO pptx_version_activations (
                    id, lesson_node_id, pptx_version_id,
                    previous_pptx_version_id, resulting_revision
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (uuid4().hex, lesson_id, version_id, previous, revision),
            )
        return _version(row), revision, changed

    def version_output_relpath(self, version_id: str) -> str:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT output_relpath
                FROM pptx_versions
                WHERE id = ? AND status IN ('publishing', 'published')
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "PPTX version output was not found"
            )
        return str(row["output_relpath"])

    @staticmethod
    def _run_query(
        connection: sqlite3.Connection,
        where: str,
        parameters: tuple[object, ...],
    ) -> sqlite3.Row | None:
        return connection.execute(
            f"SELECT run.* FROM pptx_execution_runs AS run WHERE {where}",
            parameters,
        ).fetchone()


def _deadline_has_elapsed(
    connection: sqlite3.Connection,
    deadline_at: str,
) -> bool:
    row = connection.execute(
        """
        SELECT julianday(?) <= julianday('now') AS expired
        """,
        (deadline_at,),
    ).fetchone()
    return bool(row["expired"]) if row is not None else False


def _run(row: sqlite3.Row) -> PptxExecutionRun:
    status = str(row["status"])
    actions = (
        ("resume_verification", "discard_staging")
        if status == "interrupted"
        else ("discard_staging",)
        if status in {"failed", "cancelled", "published"}
        else ()
    )
    return PptxExecutionRun(
        id=str(row["id"]),
        operation_id=str(row["operation_id"]),
        slide_plan_id=str(row["slide_plan_id"]),
        source_material_version_id=str(row["source_material_version_id"]),
        source_sha256=str(row["source_sha256"]),
        expected_slide_count=int(row["expected_slide_count"]),
        status=status,
        execution_report=_optional_json(row["execution_report_json"]),
        verification_report=_optional_json(row["verification_report_json"]),
        error_code=(
            str(row["error_code"]) if row["error_code"] is not None else None
        ),
        published_version_id=(
            str(row["published_version_id"])
            if row["published_version_id"] is not None
            else None
        ),
        phase=(
            str(row["phase"])
            if "phase" in row.keys()
            else "done" if status in {"published", "failed", "cancelled", "interrupted"}
            else "copying"
        ),
        cancel_requested=(
            bool(row["cancel_requested"])
            if "cancel_requested" in row.keys()
            else False
        ),
        staging_retained=False,
        recovery_actions=actions,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=(
            str(row["finished_at"]) if row["finished_at"] is not None else None
        ),
    )


def _version(row: sqlite3.Row) -> PptxVersion:
    return PptxVersion(
        id=str(row["id"]),
        slide_plan_id=str(row["slide_plan_id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        execution_run_id=str(row["execution_run_id"]),
        version_number=int(row["version_number"]),
        status=str(row["status"]),
        output_filename=str(row["output_filename"]),
        output_sha256=(
            str(row["output_sha256"])
            if row["output_sha256"] is not None
            else None
        ),
        slide_count=int(row["slide_count"]),
        verification_report=json.loads(
            str(row["verification_report_json"])
        ),
        created_at=str(row["created_at"]),
        published_at=(
            str(row["published_at"]) if row["published_at"] is not None else None
        ),
    )


def _optional_json(value: object) -> dict[str, object] | None:
    return json.loads(str(value)) if value is not None else None


def _elapsed_ms(
    started_at: str,
    finished_at: str | None,
    *,
    now: datetime,
) -> int:
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = (
        datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        if finished_at is not None
        else now
    )
    return max(0, int(round((finished - started).total_seconds() * 1000)))


def _json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["PptxExecutionRepository"]
