from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.repositories.grading_database import open_grading_repositories


@pytest.fixture
def job_manager(tmp_path):
    manager = JobManager(JobStore(tmp_path / "jobs.db"), cleanup_interrupted=False, max_workers=1)
    try:
        yield manager
    finally:
        manager.shutdown()


def _submission(manager, method, job_type, monkeypatch):
    """Prepare valid synthetic inputs, keeping scheduling on the real SQLite store."""
    payload = {
        "session_id": 1,
        "client_request_token": "a" * 32,
        "client_request_fingerprint": "b" * 64,
        "mode": "generate",
    }
    manager.register(job_type, lambda context: {"completed": True})
    if method in {"submit", "submit_unique_active", "submit_idempotent_export"}:
        return lambda: getattr(manager, method)(job_type, payload)
    if method == "start_existing":
        job = manager.store.create_job(job_type, payload)
        return lambda: manager.start_existing(job.id)
    if method == "submit_idempotent_scan_start":
        # Template validation is covered by the scan API tests. Isolate launching
        # its committed job here, with all later transitions persisted in SQLite.
        monkeypatch.setattr(
            manager.store,
            "create_idempotent_scan_grading_start",
            lambda data: (manager.store.create_job(job_type, data), True),
        )
    elif method == "submit_idempotent_question_bank_sync":
        payload.update(mode="sync", source_paper_sha256="c" * 64, config_revision="d" * 64)
    elif method == "submit_idempotent_taxonomy_suggestion":
        payload.update(run_id="c" * 32, operation="process")
    elif method == "submit_idempotent_tagging_sync":
        payload.update(question_ids=[1])
    elif method in {"submit_config_retry", "submit_idempotent_config_retry"}:
        source = manager.store.create_job("config_generation", {"session_id": 1})
        manager.store.finish(source.id, "failed", "synthetic source failure")
        payload.update(mode="retry", source_job_id=source.id)
    return lambda: getattr(manager, method)(payload)


@pytest.mark.parametrize(
    ("method", "job_type", "error_text"),
    [
        ("submit", "synthetic", "synthetic scheduling failure"),
        ("submit", "config_generation", "synthetic scheduling failure"),
        ("start_existing", "synthetic", "synthetic scheduling failure"),
        ("submit_unique_active", "synthetic", "synthetic scheduling failure"),
        ("submit_idempotent_scan_start", "grading_run", "grading job could not be scheduled"),
        ("submit_idempotent_config", "config_generation", "synthetic scheduling failure"),
        ("submit_idempotent_question_bank_sync", "question_bank_sync", "question-bank sync job could not be scheduled"),
        ("submit_idempotent_taxonomy_suggestion", "taxonomy_suggestion", "taxonomy suggestion job could not be scheduled"),
        ("submit_idempotent_tagging_sync", "tagging_sync", "tagging sync job could not be scheduled"),
        ("submit_config_retry", "config_generation", "synthetic scheduling failure"),
        ("submit_idempotent_config_retry", "config_generation", "synthetic scheduling failure"),
        ("submit_idempotent_export", "wrong_question_export", "synthetic scheduling failure"),
    ],
)
def test_submission_failure_is_persisted_and_does_not_leave_waiting_job(
    job_manager, monkeypatch, method, job_type, error_text,
):
    submit = _submission(job_manager, method, job_type, monkeypatch)

    def reject(*args, **kwargs):
        raise RuntimeError("synthetic scheduling failure")

    monkeypatch.setattr(job_manager._executor, "submit", reject)
    monkeypatch.setattr(job_manager._interactive_executor, "submit", reject)
    with pytest.raises(RuntimeError, match=error_text):
        submit()
    jobs, _ = job_manager.list()
    current = jobs[0]
    assert current.job_type == job_type
    assert current.status == "failed"
    assert current.error == "job scheduling failed"
    assert current.finished_at is not None
    assert not any(job.status == "queued" for job in jobs)
    assert job_manager.start_existing(current.id).status == "failed"


def test_instant_completion_returns_result_without_locking_cleanup(job_manager, monkeypatch):
    calls = []
    job_manager.register("instant", lambda context: calls.append(context.job_id) or {"answer": 42})

    class CompletedFuture(Future):
        def add_done_callback(self, callback):
            # Fail promptly instead of hanging pytest if callback registration
            # is moved under the lock that its immediate cleanup also needs.
            assert not job_manager._lock.locked()
            super().add_done_callback(callback)

    def complete(run, *args):
        future = CompletedFuture()
        run(*args)
        future.set_result(None)
        return future

    monkeypatch.setattr(job_manager._executor, "submit", complete)
    job = job_manager.submit("instant")
    job_manager.wait(job.id, timeout=1)
    assert job_manager.get(job.id).status == "succeeded"
    assert job_manager.get(job.id).result == {"answer": 42}
    assert job_manager.start_existing(job.id).status == "succeeded"
    assert calls == [job.id]


def test_duplicate_config_runs_once_while_scan_uses_independent_pool(job_manager):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def configure(context):
        calls.append(context.job_id)
        entered.set()
        assert release.wait(10)
        return {"generated": True}

    job_manager.register("config_generation", configure)
    job_manager.register("scan_analysis", lambda context: {"scan_ready": True})
    payload = {"session_id": 1, "mode": "generate", "client_request_token": "a" * 32,
               "client_request_fingerprint": "b" * 64}
    try:
        job, created = job_manager.submit_idempotent_config(payload)
        assert created and entered.wait(3)
        with ThreadPoolExecutor(max_workers=4) as callers:
            replays = list(callers.map(lambda _: job_manager.submit_idempotent_config(payload), range(4)))
        assert all(replayed.id == job.id and not new for replayed, new in replays)
        assert job_manager.start_existing(job.id).id == job.id
        scan = job_manager.submit("scan_analysis")
        job_manager.wait(scan.id, timeout=3)
        assert job_manager.get(scan.id).result == {"scan_ready": True}
        assert job_manager.get(job.id).status == "running"
    finally:
        release.set()
    job_manager.wait(job.id, timeout=3)
    assert job_manager.get(job.id).result == {"generated": True}
    assert calls == [job.id]
    repeated, created = job_manager.submit_idempotent_config(payload)
    assert not created and repeated.id == job.id


def test_export_created_before_shutdown_is_failed_instead_of_waiting(job_manager, monkeypatch):
    job_manager.register("wrong_question_export", lambda context: {})
    original_start = job_manager.start_existing

    def shutdown_before_start(job_id):
        job_manager.shutdown()
        return original_start(job_id)

    monkeypatch.setattr(job_manager, "start_existing", shutdown_before_start)
    with pytest.raises(RuntimeError, match="shut down"):
        job_manager.submit_idempotent_export("wrong_question_export", {"client_request_token": "a" * 32})
    jobs, _ = job_manager.list()
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert jobs[0].error == "job scheduling failed"


def test_failed_status_write_error_is_propagated(job_manager, monkeypatch):
    job_manager.register("synthetic", lambda context: {})

    def reject(*args, **kwargs):
        raise RuntimeError("synthetic scheduling failure")

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic database write unavailable")

    monkeypatch.setattr(job_manager._executor, "submit", reject)
    monkeypatch.setattr(job_manager.store, "finish", unavailable)
    with pytest.raises(sqlite3.OperationalError, match="synthetic database write unavailable"):
        job_manager.submit("synthetic")


def test_job_manager_restart_removes_only_interrupted_automatic_question_links(
    tmp_path,
) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    
    from question_bank.database.schema import connect, initialize_database
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    grading_db_path = tmp_path / "grading.db"
    question_bank_db_path = tmp_path / "question-bank.db"
    grading_db = open_grading_repositories(grading_db_path)
    grading_db.initialize()
    session_id = grading_db.sessions.create_grading_session(
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
    grading_db.sessions.update_question_bank_sync_state(
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
            (item["source_question_id"], item["bank_question_id"]) for item in remaining
        ] == [("Q18", 202)]
        assert (
            grading_db.sessions.get_grading_session(session_id)["question_bank_sync_state"]
            == "failed"
        )
    finally:
        manager.shutdown()


def test_chapter_publication_drains_analysis_without_blocking_its_queue(job_manager):
    started = threading.Event()
    release_analysis = threading.Event()
    second_finished = threading.Event()
    publishing = threading.Event()
    release_publication = threading.Event()
    new_submission = threading.Event()

    def analysis(context):
        if context.payload.get('first'):
            started.set()
            assert release_analysis.wait(5)
        else:
            second_finished.set()
        return {'done': True}

    def publish(context):
        with job_manager.standard_publication_guard(context):
            assert second_finished.is_set()
            publishing.set()
            assert release_publication.wait(5)
        return {'published': True}

    job_manager.register('tagging_sync', analysis)
    job_manager.register('chapter_type_organize', publish)
    first = job_manager.submit('tagging_sync', {'first': True})
    assert started.wait(3)
    second = job_manager.submit('tagging_sync', {})
    chapter = job_manager.submit('chapter_type_organize', {})
    assert not publishing.is_set()
    assert job_manager.get(second.id).status == 'queued'
    release_analysis.set()
    try:
        assert publishing.wait(3)
        def submit_new():
            job_manager.submit('tagging_sync', {})
            new_submission.set()
        thread = threading.Thread(target=submit_new)
        thread.start()
        assert not new_submission.wait(.1)
    finally:
        release_publication.set()
    thread.join(3)
    assert new_submission.is_set()
    for job in (first, second, chapter):
        job_manager.wait(job.id, timeout=3)
        assert job_manager.get(job.id).status == 'succeeded'


def test_tagging_token_also_binds_confirmed_chapter_budget(job_manager):
    from backend.jobs.store import TaggingSyncJobRequestConflictError

    job_manager.register('tagging_sync', lambda context: {'done': True})
    payload = {'question_ids': [1], 'client_request_token': 'e' * 32,
               'chapter_type_authorization': {'confirmed': True, 'request_limit': 1}}
    first, created = job_manager.submit_idempotent_tagging_sync(payload)
    job_manager.wait(first.id, timeout=3)
    replay, replay_created = job_manager.submit_idempotent_tagging_sync(payload)
    assert created and not replay_created and replay.id == first.id
    with pytest.raises(TaggingSyncJobRequestConflictError):
        job_manager.submit_idempotent_tagging_sync({**payload,
            'chapter_type_authorization': {'confirmed': True, 'request_limit': 2}})


@pytest.mark.parametrize('multiple_papers', [False, True])
def test_default_handlers_automatically_publish_authorized_chapter_after_tagging(job_manager, tmp_path, multiple_papers):
    import threading
    from types import SimpleNamespace
    from backend.jobs.default_handlers import register_default_job_handlers
    from question_bank.knowledge_graph_release.repository import load_active_release
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    from tests.test_skill_candidates import (_automatic_type_bank, _automatic_response,
        _add_automatic_type_questions, _approve_automatic_type_evidence)

    bank, old_release_id = _automatic_type_bank(tmp_path)
    pending = list(range(31, 61)) if multiple_papers else []
    if pending:
        _add_automatic_type_questions(bank, tmp_path,
            [(qid, 1 if qid < 46 else 2, 'kp_bnu24_math_g8_lower_1_1') for qid in pending],
            old_release_id, available=False)
    calls, analyses = [], []
    lock = threading.Lock()
    def organize(payload):
        calls.append(payload)
        return _automatic_response(payload)
    def tagging(**kwargs):
        ids = kwargs['context'].payload['question_ids']
        with lock:
            analyses.append(ids)
            if pending:
                _approve_automatic_type_evidence(bank, tmp_path, ids, old_release_id)
        return {'outcome': 'complete', 'successful_question_ids': ids}
    register_default_job_handlers(job_manager, db_path=job_manager.store.db_path,
        reports_dir=tmp_path / 'reports', data_root=tmp_path, question_bank_db_path=bank,
        taxonomy_governance=SimpleNamespace(state_path=tmp_path / 'taxonomy.json'),
        tagging_ai_service_factory=lambda: SimpleNamespace(organize_chapter_types=organize),
        tagging_sync_runner=tagging)
    plan = preview_chapter_type_plan(db_path=bank, data_root=tmp_path,
        volume_id='bnu24-math-g8-lower', question_ids=pending)
    first = None
    if pending:
        first = job_manager.submit('tagging_sync', {'question_ids': pending[:15]})
    analysis = job_manager.submit('tagging_sync', {'question_ids': pending[15:] if pending else [1],
        'chapter_type_authorization': {**plan, 'confirmed': True, 'request_limit': plan['planned_requests']}})
    job_manager.wait(analysis.id, timeout=30)
    completed = job_manager.get(analysis.id)
    assert completed.status == 'succeeded'
    chapter_job = job_manager.get(completed.result['chapter_type_job_id'])
    job_manager.wait(chapter_job.id, timeout=30)
    published = job_manager.get(chapter_job.id)
    assert published.status == 'succeeded'
    assert published.result['outcome'] == 'published'
    assert published.result['published_count'] == (60 if pending else 30)
    assert published.result['model_calls'] == 1 == len(calls)
    assert load_active_release(bank).release_id != old_release_id
    if first:
        assert job_manager.get(first.id).status == 'succeeded'
        assert 'chapter_type_job_id' not in job_manager.get(first.id).result
        assert job_manager.store.list_jobs(job_types=('chapter_type_organize',))[1] == 1
        assert sorted(qid for batch in analyses for qid in batch) == pending
        assert len(calls[0]['questions']) == 60
