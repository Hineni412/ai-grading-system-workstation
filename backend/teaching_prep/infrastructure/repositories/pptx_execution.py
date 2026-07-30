from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
)
from backend.teaching_prep.domain.models import PptxExecutionRun, PptxVersion
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
    ) -> PptxVersion:
        with self._database.connect(immediate=True) as connection:
            version_cursor = connection.execute(
                """
                UPDATE pptx_versions
                SET status = 'published',
                    output_sha256 = ?,
                    published_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'publishing'
                """,
                (output_sha256, version_id),
            )
            run_cursor = connection.execute(
                """
                UPDATE pptx_execution_runs
                SET status = 'published',
                    error_code = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status IN ('publishing', 'interrupted')
                  AND published_version_id = ?
                """,
                (run_id, version_id),
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
                """,
                (run_id,),
            )
            if (
                version_cursor.rowcount != 1
                or run_cursor.rowcount != 1
                or operation_cursor.rowcount != 1
            ):
                raise TeachingPrepConflictError(
                    "PPTX publication state changed"
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
                    error_code = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status IN ('running', 'verifying', 'publishing')
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
                WHERE operation_id = ? AND status = 'running'
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


def _json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["PptxExecutionRepository"]
