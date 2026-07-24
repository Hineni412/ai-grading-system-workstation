from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def test_job_store_creates_and_reads_job_records(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")

    job = store.create_job("report_export", {"session_id": 7})
    loaded = store.get_job(job.id)

    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.job_type == "report_export"
    assert loaded.payload == {"session_id": 7}
    assert loaded.status == "queued"
    assert loaded.progress == 0.0
    assert loaded.stage == "queued"


def test_job_store_session_filter_only_accepts_json_positive_integer(tmp_path) -> None:
    from backend.jobs.store import JobStore

    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    rejected_payloads = (
        '{"session_id":',
        '{"session_id": true}',
        '{"session_id": 7.0}',
        '{"session_id": 0}',
        '{"session_id": -7}',
        '{"session_id": "7garbage"}',
        '{"session_id": " 7 "}',
        '{"session_id": 7e0}',
        '{"session_id": "7e0"}',
        '{"session_id": "7"}',
    )
    with sqlite3.connect(db_path) as conn:
        for payload_json in rejected_payloads:
            conn.execute(
                "INSERT INTO jobs (job_type, payload_json, status) "
                "VALUES ('grading_run', ?, 'queued')",
                (payload_json,),
            )
        cursor = conn.execute(
            "INSERT INTO jobs (job_type, payload_json, status) "
            "VALUES ('grading_run', ?, 'queued')",
            ('{"session_id": 7}',),
        )
        accepted_id = int(cursor.lastrowid)

    jobs, total = store.list_jobs(session_id=7, limit=20, offset=0)

    assert total == 1
    assert [job.id for job in jobs] == [accepted_id]


def test_job_store_updates_progress_and_finishes(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("scan_analysis", {})

    assert store.mark_running(job.id) is True
    store.update_progress(job.id, progress=0.45, stage="scan", detail="reading pages")
    store.finish(job.id, "succeeded", result={"file_path": "reports/out.xlsx"})

    loaded = store.get_job(job.id)
    assert loaded.status == "succeeded"
    assert loaded.progress == 1.0
    assert loaded.stage == "scan"
    assert loaded.detail == "reading pages"
    assert loaded.result == {"file_path": "reports/out.xlsx"}
    assert loaded.finished_at is not None


def test_job_store_records_cancellation_request(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("grading_run", {"session_id": 3})

    assert store.request_cancel(job.id) is True

    loaded = store.get_job(job.id)
    assert loaded.cancel_requested is True
    assert loaded.status == "cancelled"
    assert store.is_cancel_requested(job.id) is True


def test_running_cancel_stays_running_until_handler_confirms(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("grading_run", {"session_id": 3})
    assert store.mark_running(job.id) is True

    assert store.request_cancel(job.id) is True

    requested = store.get_job(job.id)
    assert requested is not None
    assert requested.status == "running"
    assert requested.cancel_requested is True
    assert requested.started_at is not None
    assert requested.finished_at is None

    assert store.confirm_cancelled(job.id) is True
    cancelled = store.get_job(job.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None


def test_cancelled_job_cannot_be_overwritten_by_late_success(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("report_export", {})
    assert store.request_cancel(job.id) is True

    store.finish(job.id, "succeeded", result={"published": True})

    loaded = store.get_job(job.id)
    assert loaded is not None
    assert loaded.status == "cancelled"
    assert loaded.result == {}


def test_job_store_marks_interrupted_queued_and_running_jobs_failed(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    queued = store.create_job("config_generation", {})
    running = store.create_job("report_export", {})
    store.mark_running(running.id)
    done = store.create_job("training_export", {})
    store.finish(done.id, "succeeded")

    assert store.fail_interrupted_jobs() == 2

    assert store.get_job(queued.id).status == "failed"
    assert store.get_job(running.id).status == "failed"
    assert store.get_job(done.id).status == "succeeded"


def test_restart_marks_unconfirmed_running_cancel_request_failed(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    running = store.create_job("grading_run", {})
    assert store.mark_running(running.id) is True
    assert store.request_cancel(running.id) is True

    assert store.fail_interrupted_jobs() == 1

    loaded = store.get_job(running.id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.cancel_requested is True
    assert loaded.error == "interrupted by process restart"
    assert loaded.finished_at is not None


def test_job_store_rejects_invalid_finish_status(tmp_path) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("report_export", {})

    with pytest.raises(ValueError):
        store.finish(job.id, "done")


def test_job_store_schema_matches_migration_sql(tmp_path) -> None:
    from backend.jobs.store import JobStore

    runtime_db = tmp_path / "runtime.db"
    migration_db = tmp_path / "migration.db"

    JobStore(runtime_db).initialize()
    with sqlite3.connect(migration_db) as conn:
        sql = Path.cwd() / "migrations" / "grading" / "003_add_jobs.sql"
        conn.executescript(sql.read_text(encoding="utf-8"))

    with sqlite3.connect(runtime_db) as runtime, sqlite3.connect(migration_db) as migrated:
        runtime_cols = runtime.execute("PRAGMA table_info(jobs)").fetchall()
        migrated_cols = migrated.execute("PRAGMA table_info(jobs)").fetchall()
        assert json.dumps(runtime_cols, default=str) == json.dumps(migrated_cols, default=str)


def test_job_store_records_current_grading_migration(tmp_path: Path) -> None:
    from backend.jobs.store import JobStore

    database = tmp_path / "jobs.db"
    JobStore(database)

    with sqlite3.connect(database) as conn:
        current = conn.execute(
            "SELECT migration_name FROM schema_migrations "
            "WHERE success = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert current == ("005_add_status_constraints",)


def test_job_store_preserves_legacy_rows_when_adding_result_json(tmp_path: Path) -> None:
    from backend.jobs.store import JobStore

    canonical = Path.cwd() / "migrations" / "grading" / "003_add_jobs.sql"
    migration_sql = canonical.read_text(encoding="utf-8")
    legacy_sql = migration_sql.replace(
        "    result_json TEXT NOT NULL DEFAULT '{}',\n",
        "",
    )
    assert legacy_sql != migration_sql

    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(legacy_sql)
        cursor = conn.execute(
            "INSERT INTO jobs (job_type, payload_json, status) "
            "VALUES (?, ?, 'queued')",
            ("report_export", '{"session_id": 7}'),
        )
        job_id = int(cursor.lastrowid)

    store = JobStore(db_path)

    loaded = store.get_job(job_id)
    assert loaded is not None
    assert loaded.job_type == "report_export"
    assert loaded.payload == {"session_id": 7}
    assert loaded.status == "queued"
    assert loaded.result == {}
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "result_json" in columns


def test_job_store_closes_every_sqlite_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs import store as store_module

    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    class TrackingConnection(sqlite3.Connection):
        was_closed = False

        def close(self) -> None:
            self.was_closed = True
            super().close()

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(store_module.sqlite3, "connect", tracking_connect)
    try:
        store = store_module.JobStore(tmp_path / "jobs.db")
        job = store.create_job("report_export", {})
        assert store.get_job(job.id) is not None

        assert opened
        assert all(connection.was_closed for connection in opened)
    finally:
        for connection in opened:
            if not connection.was_closed:
                connection.close()
