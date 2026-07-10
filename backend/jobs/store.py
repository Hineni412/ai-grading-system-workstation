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
