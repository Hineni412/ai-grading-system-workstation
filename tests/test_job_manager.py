from __future__ import annotations

import sqlite3
import threading
import time

import pytest


def test_job_manager_runs_registered_handler_and_records_success(tmp_path) -> None:
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)

    def handler(context: JobContext) -> None:
        context.report(0.5, "halfway", "working")

    manager.register("report_export", handler)
    job = manager.submit("report_export", {"session_id": 1})

    manager.wait(job.id, timeout=5)

    loaded = store.get_job(job.id)
    assert loaded.status == "succeeded"
    assert loaded.progress == 1.0
    assert loaded.stage == "halfway"
    assert loaded.detail == "working"


def test_job_manager_restart_removes_only_interrupted_automatic_question_links(
    tmp_path,
) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager
    from question_bank.database.schema import connect, initialize_database
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    grading_db_path = tmp_path / "grading.db"
    question_bank_db_path = tmp_path / "question-bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    session_id = grading_db.create_grading_session(
        "Exam",
        "rubric.json",
        "answer.json",
    )
    store = JobStore(grading_db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
        },
    )
    assert store.mark_running(job.id)
    grading_db.update_question_bank_sync_state(
        session_id,
        state="running",
        details={
            "job_id": job.id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
            "stage": "linking",
        },
    )

    initialize_database(question_bank_db_path)
    with connect(question_bank_db_path) as conn:
        conn.executemany(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (?, ?, ?)
            """,
            [(201, "17", "automatic"), (202, "18", "manual")],
        )
    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=session_id,
        source_question_id="Q17",
        bank_question_id=201,
        link_method="source_metadata",
        evidence={"sync_job_id": job.id, "sync_config_revision": "b" * 64},
    )
    links.confirm_link(
        grading_session_id=session_id,
        source_question_id="Q18",
        bank_question_id=202,
        link_method="manual",
        reviewed_by="teacher",
    )

    manager = JobManager(
        store,
        max_workers=1,
        question_bank_db_path=question_bank_db_path,
    )
    try:
        assert store.get_job(job.id).status == "failed"
        remaining = links.list_links(session_id)
        assert [
            (item["source_question_id"], item["bank_question_id"])
            for item in remaining
        ] == [("Q18", 202)]
        assert (
            grading_db.get_grading_session(session_id)[
                "question_bank_sync_state"
            ]
            == "failed"
        )
    finally:
        manager.shutdown()


def test_job_manager_restart_keeps_committed_question_links(tmp_path) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager
    from question_bank.database.schema import connect, initialize_database
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    grading_db_path = tmp_path / "grading.db"
    question_bank_db_path = tmp_path / "question-bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    session_id = grading_db.create_grading_session(
        "Exam",
        "rubric.json",
        "answer.json",
    )
    store = JobStore(grading_db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
        },
    )
    assert store.mark_running(job.id)
    grading_db.update_question_bank_sync_state(
        session_id,
        state="ready",
        details={
            "job_id": job.id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
            "outcome": "complete",
        },
    )

    initialize_database(question_bank_db_path)
    with connect(question_bank_db_path) as conn:
        conn.execute(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (201, '17', 'committed')
            """
        )
    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=session_id,
        source_question_id="Q17",
        bank_question_id=201,
        link_method="source_metadata",
        evidence={"sync_job_id": job.id, "sync_config_revision": "b" * 64},
    )

    manager = JobManager(
        store,
        max_workers=1,
        question_bank_db_path=question_bank_db_path,
    )
    try:
        assert links.confirmed_bank_question_ids(session_id) == {201}
        assert (
            grading_db.get_grading_session(session_id)[
                "question_bank_sync_state"
            ]
            == "ready"
        )
    finally:
        manager.shutdown()


def test_job_manager_restart_recovers_failed_job_left_owning_running_state(
    tmp_path,
) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager
    from question_bank.database.schema import connect, initialize_database
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    grading_db_path = tmp_path / "grading.db"
    question_bank_db_path = tmp_path / "question-bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    session_id = grading_db.create_grading_session(
        "Exam",
        "rubric.json",
        "answer.json",
    )
    store = JobStore(grading_db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
        },
    )
    assert store.mark_running(job.id)
    grading_db.update_question_bank_sync_state(
        session_id,
        state="running",
        details={
            "job_id": job.id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
            "stage": "linking",
        },
    )
    store.finish(job.id, "failed", error="rollback failed")

    initialize_database(question_bank_db_path)
    with connect(question_bank_db_path) as conn:
        conn.execute(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (201, '17', 'stale')
            """
        )
    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=session_id,
        source_question_id="Q17",
        bank_question_id=201,
        link_method="source_metadata",
        evidence={"sync_job_id": job.id, "sync_config_revision": "b" * 64},
    )

    manager = JobManager(
        store,
        max_workers=1,
        question_bank_db_path=question_bank_db_path,
    )
    try:
        assert links.list_links(session_id) == []
        assert (
            grading_db.get_grading_session(session_id)[
                "question_bank_sync_state"
            ]
            == "failed"
        )
    finally:
        manager.shutdown()


def test_job_manager_records_handler_failure(tmp_path) -> None:
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)

    def handler(_context: JobContext) -> None:
        raise RuntimeError("boom")

    manager.register("scan_analysis", handler)
    job = manager.submit("scan_analysis", {})

    manager.wait(job.id, timeout=5)

    loaded = store.get_job(job.id)
    assert loaded.status == "failed"
    assert "boom" in loaded.error


def test_job_manager_marks_unserializable_handler_result_as_failed(tmp_path) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    manager.register("report_export", lambda _context: {"unserializable": object()})
    try:
        job = manager.submit("report_export", {})

        manager.wait(job.id, timeout=5)

        loaded = store.get_job(job.id)
        assert loaded is not None
        assert loaded.status == "failed"
        assert loaded.result == {}
        assert loaded.error == "Job result could not be persisted."
    finally:
        manager.shutdown()


def test_job_manager_releases_completed_futures_without_wait(tmp_path) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=2)
    manager.register("quick", lambda _context: {"done": True})
    try:
        jobs = [manager.submit("quick", {"index": index}) for index in range(8)]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            statuses = [store.get_job(job.id).status for job in jobs]
            with manager._lock:
                retained_futures = len(manager._futures)
            if all(status == "succeeded" for status in statuses) and retained_futures == 0:
                break
            time.sleep(0.01)

        assert all(store.get_job(job.id).status == "succeeded" for job in jobs)
        with manager._lock:
            assert manager._futures == {}
    finally:
        manager.shutdown()


def test_job_manager_fails_unrequested_cancellation_signal(tmp_path) -> None:
    from backend.jobs.manager import (
        JobCancellationRequested,
        JobContext,
        JobManager,
    )
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)

    def handler(_context: JobContext) -> None:
        raise JobCancellationRequested("spurious cancellation signal")

    manager.register("scan_analysis", handler)
    job = manager.submit("scan_analysis", {})
    manager.wait(job.id, timeout=5)

    loaded = store.get_job(job.id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.cancel_requested is False
    assert loaded.error == "spurious cancellation signal"


def test_job_manager_rejects_unsupported_job_type(tmp_path) -> None:
    from backend.jobs.manager import JobManager, UnsupportedJobTypeError
    from backend.jobs.store import JobStore

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    with pytest.raises(UnsupportedJobTypeError):
        manager.submit("missing_type", {})


def test_job_manager_exposes_cancel_request_to_running_handler(tmp_path) -> None:
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    started = threading.Event()
    saw_cancel = threading.Event()
    allow_confirm = threading.Event()

    def handler(context: JobContext) -> None:
        started.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if context.cancel_requested:
                saw_cancel.set()
                assert allow_confirm.wait(3)
                context.raise_if_cancelled()
            time.sleep(0.01)
        raise AssertionError("cancel request was not visible to handler")

    manager.register("grading_run", handler)
    job = manager.submit("grading_run", {})
    assert started.wait(3)

    assert manager.cancel(job.id) is True
    try:
        requested = store.get_job(job.id)
        assert requested.status == "running"
        assert requested.finished_at is None
    finally:
        allow_confirm.set()
    manager.wait(job.id, timeout=5)

    loaded = store.get_job(job.id)
    assert saw_cancel.is_set()
    assert loaded.status == "cancelled"
    assert loaded.cancel_requested is True


def test_job_manager_records_failure_after_cancel_request_as_failed(tmp_path) -> None:
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def handler(_context: JobContext) -> None:
        started.set()
        assert release.wait(3)
        raise RuntimeError("boom after request")

    manager.register("scan_analysis", handler)
    try:
        job = manager.submit("scan_analysis", {})
        assert started.wait(3)
        assert manager.cancel(job.id) is True
        release.set()
        manager.wait(job.id, timeout=5)

        loaded = store.get_job(job.id)
        assert loaded is not None
        assert loaded.status == "failed"
        assert loaded.cancel_requested is True
        assert loaded.error == "boom after request"
    finally:
        release.set()
        manager.shutdown()


def test_job_manager_normal_completion_can_win_cancel_race(tmp_path) -> None:
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    committed = threading.Event()
    release = threading.Event()

    def handler(_context: JobContext) -> dict[str, bool]:
        committed.set()
        assert release.wait(3)
        return {"published": True}

    manager.register("report_export", handler)
    try:
        job = manager.submit("report_export", {})
        assert committed.wait(3)
        assert manager.cancel(job.id) is True
        release.set()
        manager.wait(job.id, timeout=5)

        loaded = store.get_job(job.id)
        assert loaded is not None
        assert loaded.status == "succeeded"
        assert loaded.cancel_requested is True
        assert loaded.result == {"published": True}
    finally:
        release.set()
        manager.shutdown()


def test_job_manager_shutdown_is_idempotent_and_observable(tmp_path) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    assert manager.is_shutdown is False

    manager.shutdown()
    manager.shutdown()

    assert manager.is_shutdown is True


def test_job_manager_rejects_submit_after_shutdown_without_creating_job(tmp_path) -> None:
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore

    db_path = tmp_path / "jobs.db"
    manager = JobManager(JobStore(db_path), max_workers=1)

    def handler(_context: JobContext) -> None:
        return None

    manager.register("report_export", handler)
    manager.shutdown()

    with pytest.raises(RuntimeError, match="shut down"):
        manager.submit("report_export", {})

    with sqlite3.connect(db_path) as connection:
        job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert job_count == 0


def test_submit_and_shutdown_cannot_leave_an_orphaned_queued_job(tmp_path) -> None:
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore

    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    manager = JobManager(store, max_workers=1)
    create_entered = threading.Event()
    allow_create = threading.Event()
    shutdown_waiting = threading.Event()
    submitted = []
    errors = []

    class ObservableLock:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._owner: int | None = None

        def __enter__(self):
            if threading.current_thread().name == "job-manager-shutdown":
                shutdown_waiting.set()
            self._lock.acquire()
            self._owner = threading.get_ident()
            return self

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            self._owner = None
            self._lock.release()

        def held_by_current_thread(self) -> bool:
            return self._owner == threading.get_ident()

    observable_lock = ObservableLock()
    manager._lock = observable_lock
    real_create_job = store.create_job
    real_executor_submit = manager._executor.submit

    def blocked_create_job(job_type, payload):
        create_entered.set()
        assert allow_create.wait(3)
        return real_create_job(job_type, payload)

    store.create_job = blocked_create_job

    def guarded_executor_submit(*args, **kwargs):
        assert observable_lock.held_by_current_thread(), (
            "executor submission must remain inside the shutdown lock"
        )
        return real_executor_submit(*args, **kwargs)

    manager._executor.submit = guarded_executor_submit

    def handler(_context: JobContext) -> None:
        return None

    def submit_job() -> None:
        try:
            submitted.append(manager.submit("report_export", {}))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    manager.register("report_export", handler)
    submit_thread = threading.Thread(target=submit_job, name="job-manager-submit")
    shutdown_thread = threading.Thread(
        target=manager.shutdown,
        name="job-manager-shutdown",
    )
    try:
        submit_thread.start()
        assert create_entered.wait(3)
        shutdown_thread.start()
        assert shutdown_waiting.wait(3)
        allow_create.set()
        submit_thread.join(5)
        shutdown_thread.join(5)

        assert not submit_thread.is_alive()
        assert not shutdown_thread.is_alive()
        assert errors == []
        assert len(submitted) == 1
        assert manager.is_shutdown is True
        assert store.get_job(submitted[0].id).status == "succeeded"
        with sqlite3.connect(db_path) as connection:
            queued_count = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'queued'"
            ).fetchone()[0]
        assert queued_count == 0
    finally:
        allow_create.set()
        submit_thread.join(5)
        if shutdown_thread.ident is not None:
            shutdown_thread.join(5)
        manager.shutdown()
