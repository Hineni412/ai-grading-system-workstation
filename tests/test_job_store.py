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


def test_taxonomy_suggestion_same_active_operation_returns_existing_job(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    run_id = "a" * 32
    first, first_created = store.create_idempotent_taxonomy_suggestion_job(
        {
            "run_id": run_id,
            "operation": "process",
            "client_request_token": "1" * 32,
        }
    )

    repeated, repeated_created = store.create_idempotent_taxonomy_suggestion_job(
        {
            "run_id": run_id,
            "operation": "process",
            "client_request_token": "2" * 32,
        }
    )

    assert first_created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert repeated.payload["client_request_token"] == "1" * 32


def test_taxonomy_suggestion_different_active_operation_remains_busy(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore, TaxonomySuggestionJobBusyError

    store = JobStore(tmp_path / "jobs.db")
    run_id = "b" * 32
    store.create_idempotent_taxonomy_suggestion_job(
        {
            "run_id": run_id,
            "operation": "process",
            "client_request_token": "3" * 32,
        }
    )

    with pytest.raises(TaxonomySuggestionJobBusyError):
        store.create_idempotent_taxonomy_suggestion_job(
            {
                "run_id": run_id,
                "operation": "retry",
                "client_request_token": "4" * 32,
            }
        )


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


def test_job_store_finds_latest_exact_payload_match_without_returning_history(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore

    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    first = store.create_job(
        "teaching_prep.semester_mapping",
        {
            "semester_id": "semester-1",
            "material_record_id": "a" * 32,
            "source_state_sha256": "1" * 64,
        },
    )
    store.create_job(
        "teaching_prep.semester_mapping",
        {
            "semester_id": "semester-2",
            "material_record_id": "a" * 32,
            "source_state_sha256": "1" * 64,
        },
    )
    latest = store.create_job(
        "teaching_prep.semester_mapping",
        {
            "semester_id": "semester-1",
            "material_record_id": "a" * 32,
            "source_state_sha256": "1" * 64,
        },
    )
    store.finish(latest.id, "succeeded")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO jobs (job_type, payload_json, status) VALUES (?, ?, ?)",
            ("teaching_prep.semester_mapping", "{broken", "succeeded"),
        )

    active = store.find_latest_job_by_payload(
        job_type="teaching_prep.semester_mapping",
        payload_equals={
            "semester_id": "semester-1",
            "material_record_id": "a" * 32,
            "source_state_sha256": "1" * 64,
        },
        statuses=("queued", "running", "paused", "succeeded"),
    )

    assert active is not None
    assert active.id == latest.id
    assert active.id != first.id


def test_tagging_sync_keeps_token_conflict_and_active_signature_rules(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore, TaggingSyncJobRequestConflictError

    store = JobStore(tmp_path / "jobs.db")
    first, created = store.create_idempotent_tagging_sync_job(
        {
            "question_ids": [2, 1, 1],
            "client_request_token": "5" * 32,
        }
    )
    same_work, repeated = store.create_idempotent_tagging_sync_job(
        {
            "question_ids": [1, 2],
            "client_request_token": "6" * 32,
        }
    )

    assert created is True
    assert repeated is False
    assert same_work.id == first.id

    with pytest.raises(TaggingSyncJobRequestConflictError):
        store.create_idempotent_tagging_sync_job(
            {
                "question_ids": [3],
                "client_request_token": "5" * 32,
            }
        )


def test_job_store_lists_only_latest_job_for_each_payload_identity(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore

    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    old_a = store.create_job(
        "teaching_prep.material_parse",
        {"material_version_id": "a" * 32},
    )
    latest_b = store.create_job(
        "teaching_prep.material_parse",
        {"material_version_id": "b" * 32},
    )
    latest_a = store.create_job(
        "teaching_prep.material_parse",
        {"material_version_id": "a" * 32},
    )
    store.create_job("unrelated", {"material_version_id": "c" * 32})
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO jobs (job_type, payload_json, status) VALUES (?, ?, ?)",
            ("teaching_prep.material_parse", "{broken", "succeeded"),
        )
        connection.execute(
            "INSERT INTO jobs (job_type, payload_json, status) VALUES (?, ?, ?)",
            (
                "teaching_prep.material_parse",
                json.dumps({"material_version_id": " short "}),
                "succeeded",
            ),
        )

    jobs = store.list_latest_jobs_by_payload_key(
        job_type="teaching_prep.material_parse",
        payload_key="material_version_id",
        identity_length=32,
    )

    assert [job.id for job in jobs] == [latest_a.id, latest_b.id]
    assert old_a.id not in {job.id for job in jobs}


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


def test_restart_reconciles_owned_question_bank_sync_running_state(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db_path = tmp_path / "jobs.db"
    db = DBManager(db_path)
    db.initialize()
    session_id = db.create_grading_session("Exam", "rubric.json", "answer.json")
    store = JobStore(db_path)
    source_sha256 = "a" * 64
    config_revision = "b" * 64
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "source_paper_sha256": source_sha256,
            "config_revision": config_revision,
        },
    )
    assert store.mark_running(job.id)
    db.update_question_bank_sync_state(
        session_id,
        state="running",
        details={
            "job_id": job.id,
            "source_paper_sha256": source_sha256,
            "config_revision": config_revision,
            "stage": "importing",
        },
    )

    assert store.fail_interrupted_jobs() == 1

    current = db.get_grading_session(session_id)
    assert current is not None
    assert current["question_bank_sync_state"] == "failed"
    assert current["question_bank_sync_error"] == "interrupted by process restart"
    assert json.loads(current["question_bank_sync_details_json"]) == {
        "config_revision": config_revision,
        "job_id": job.id,
        "reason": "process_restart",
        "retryable": True,
        "source_paper_sha256": source_sha256,
        "stage": "interrupted",
    }


def test_scan_grading_job_insert_rejects_a_changed_config_binding(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db_path = tmp_path / "jobs.db"
    db = DBManager(db_path)
    db.initialize()
    session_id = db.create_grading_session("Exam", "rubric-old.json", "answer-old.json")
    template_id = db.upsert_session_template(
        session_id,
        "template-front.png",
        "template-back.png",
    )
    db.mark_template_confirmed(session_id, True)
    store = JobStore(db_path)
    payload = {
        "session_id": session_id,
        "scan_batch_id": "c" * 32,
        "config_revision": "d" * 64,
        "expected_rubric_path": "rubric-old.json",
        "expected_answer_key_path": "answer-old.json",
        "expected_template_id": template_id,
        "expected_front_template_path": "template-front.png",
        "expected_back_template_path": "template-back.png",
    }
    assert db.publish_grading_session_config(
        session_id,
        rubric_path="rubric-new.json",
        answer_key_path="answer-new.json",
        expected_rubric_path="rubric-old.json",
        expected_answer_key_path="answer-old.json",
    )

    with pytest.raises(RuntimeError, match="changed"):
        store.create_idempotent_scan_grading_start(payload)


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

    migration_root = Path.cwd() / "migrations" / "grading"
    latest = sorted(path.stem for path in migration_root.glob("*.sql"))[-1]

    with sqlite3.connect(database) as conn:
        current = conn.execute(
            "SELECT migration_name FROM schema_migrations "
            "WHERE success = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert current == (latest,)


def test_job_store_rejects_legacy_database_without_migration_history(
    tmp_path: Path,
) -> None:
    from backend.jobs.store import JobStore
    from backend.schema_migrations import SchemaVersionError

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

    # JobStore 不再静默迁移没有 schema_migrations 记录的遗留库:
    # 这类库必须走受保护的维护迁移,直接打开会被拒绝且数据保持原样。
    with pytest.raises(SchemaVersionError, match="migration history is missing"):
        JobStore(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT job_type, payload_json, status FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert row == ("report_export", '{"session_id": 7}', "queued")
    assert "result_json" not in columns
    assert "schema_migrations" not in tables


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
