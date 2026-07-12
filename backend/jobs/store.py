from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


JOB_STATUSES = ("queued", "running", "paused", "succeeded", "failed", "cancelled")
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class ConfigRetryAlreadySubmittedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: int
    job_type: str
    payload: dict[str, Any]
    result: dict[str, Any]
    status: str
    progress: float
    stage: str
    detail: str
    error: str | None
    cancel_requested: bool
    created_at: str
    started_at: str | None
    updated_at: str
    finished_at: str | None


_JOBS_SCHEMA_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "grading"
    / "003_add_jobs.sql"
)


def _load_jobs_schema_sql() -> str:
    return _JOBS_SCHEMA_MIGRATION_PATH.read_text(encoding="utf-8")


class JobStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_load_jobs_schema_sql())
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "result_json" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'"
                )

    def create_job(self, job_type: str, payload: dict[str, Any] | None) -> JobRecord:
        clean_type = str(job_type or "").strip()
        if not clean_type:
            raise ValueError("job_type must be nonblank")
        payload_json = json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (job_type, payload_json, status)
                VALUES (?, ?, 'queued')
                """,
                (clean_type, payload_json),
            )
            job_id = int(cursor.lastrowid)
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded

    def create_config_retry_job(self, payload: dict[str, Any]) -> JobRecord:
        source_job_id = int(payload.get("source_job_id") or 0)
        if source_job_id <= 0:
            raise ValueError("source_job_id must be a positive integer")
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT status, payload_json FROM jobs WHERE job_type = 'config_generation'"
                ).fetchall()
                for row in rows:
                    try:
                        existing_payload = json.loads(str(row["payload_json"] or "{}"))
                    except json.JSONDecodeError:
                        continue
                    try:
                        existing_source_id = int(
                            existing_payload.get("source_job_id") or 0
                        ) if isinstance(existing_payload, dict) else 0
                    except (TypeError, ValueError):
                        existing_source_id = 0
                    if (
                        isinstance(existing_payload, dict)
                        and existing_source_id == source_job_id
                        and str(row["status"]) not in {"failed", "cancelled"}
                    ):
                        raise ConfigRetryAlreadySubmittedError(
                            f"retry already submitted for config generation job {source_job_id}"
                        )
                cursor = conn.execute(
                    """
                    INSERT INTO jobs (job_type, payload_json, status)
                    VALUES ('config_generation', ?, 'queued')
                    """,
                    (payload_json,),
                )
                job_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded

    def get_job(self, job_id: int) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        return _job_record(row) if row is not None else None

    def mark_running(self, job_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    stage = CASE WHEN stage = 'queued' THEN 'running' ELSE stage END,
                    started_at = COALESCE(started_at, datetime('now','localtime')),
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND status = 'queued' AND cancel_requested = 0
                """,
                (int(job_id),),
            )
            return cursor.rowcount == 1

    def update_progress(
        self,
        job_id: int,
        *,
        progress: float,
        stage: str,
        detail: str = "",
    ) -> None:
        bounded_progress = min(1.0, max(0.0, float(progress)))
        clean_stage = str(stage or "").strip() or "running"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET progress = ?,
                    stage = ?,
                    detail = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND status NOT IN ('succeeded','failed','cancelled')
                """,
                (bounded_progress, clean_stage, str(detail or ""), int(job_id)),
            )

    def finish(
        self,
        job_id: int,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        clean_status = str(status or "").strip()
        if clean_status not in TERMINAL_STATUSES:
            raise ValueError(f"unsupported terminal job status: {status}")
        progress = 1.0 if clean_status == "succeeded" else None
        result_json = json.dumps(dict(result or {}), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    progress = COALESCE(?, progress),
                    result_json = ?,
                    error = ?,
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE id = ?
                  AND status NOT IN ('succeeded','failed','cancelled')
                """,
                (clean_status, progress, result_json, error, int(job_id)),
            )

    def request_cancel(self, job_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET cancel_requested = CASE
                        WHEN status IN ('queued','running','paused') THEN 1
                        ELSE cancel_requested
                    END,
                    status = CASE
                        WHEN status IN ('queued','paused') THEN 'cancelled'
                        ELSE status
                    END,
                    updated_at = CASE
                        WHEN status IN ('queued','running','paused')
                            THEN datetime('now','localtime')
                        ELSE updated_at
                    END,
                    finished_at = CASE
                        WHEN status IN ('queued','paused')
                            THEN COALESCE(finished_at, datetime('now','localtime'))
                        ELSE finished_at
                    END
                WHERE id = ?
                """,
                (int(job_id),),
            )
            return cursor.rowcount == 1

    def confirm_cancelled(self, job_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled',
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE id = ?
                  AND status = 'running'
                  AND cancel_requested = 1
                """,
                (int(job_id),),
            )
            return cursor.rowcount == 1

    def is_cancel_requested(self, job_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
        return bool(row and int(row["cancel_requested"]))

    def fail_interrupted_jobs(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = COALESCE(error, 'interrupted by process restart'),
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE status IN ('queued','running')
                """
            )
            return int(cursor.rowcount)

    def finish_config_generation_and_bind(
        self,
        job_id: int,
        *,
        session_id: int,
        expected_rubric_path: str,
        expected_answer_key_path: str,
        rubric_path: str,
        answer_key_path: str,
        result: dict[str, Any],
    ) -> bool:
        result_json = json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                job = conn.execute(
                    "SELECT status, cancel_requested FROM jobs WHERE id = ?",
                    (int(job_id),),
                ).fetchone()
                if (
                    job is None
                    or str(job["status"]) != "running"
                    or bool(int(job["cancel_requested"] or 0))
                ):
                    conn.rollback()
                    return False
                session_update = conn.execute(
                    """
                    UPDATE grading_sessions
                    SET rubric_path = ?, answer_key_path = ?,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                      AND rubric_path = ? AND answer_key_path = ?
                    """,
                    (
                        str(rubric_path),
                        str(answer_key_path),
                        int(session_id),
                        str(expected_rubric_path),
                        str(expected_answer_key_path),
                    ),
                )
                if session_update.rowcount != 1:
                    conn.rollback()
                    return False
                job_update = conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'succeeded', progress = 1.0,
                        stage = 'config_generation', detail = 'complete',
                        result_json = ?, error = NULL,
                        updated_at = datetime('now','localtime'),
                        finished_at = COALESCE(finished_at, datetime('now','localtime'))
                    WHERE id = ? AND status = 'running' AND cancel_requested = 0
                    """,
                    (result_json, int(job_id)),
                )
                if job_update.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise


def _job_record(row: sqlite3.Row) -> JobRecord:
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        result = json.loads(str(row["result_json"] or "{}"))
    except (IndexError, json.JSONDecodeError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    return JobRecord(
        id=int(row["id"]),
        job_type=str(row["job_type"]),
        payload=payload,
        result=result,
        status=str(row["status"]),
        progress=float(row["progress"] or 0.0),
        stage=str(row["stage"] or ""),
        detail=str(row["detail"] or ""),
        error=row["error"],
        cancel_requested=bool(int(row["cancel_requested"] or 0)),
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        updated_at=str(row["updated_at"]),
        finished_at=row["finished_at"],
    )
