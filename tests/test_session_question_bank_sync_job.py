from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.config_workspace.publish import load_editor_config
from backend.jobs.manager import JobCancellationRequested, JobContext, JobManager
from backend.jobs.question_bank_sync import run_session_question_bank_sync_job
from backend.jobs.store import (
    JobStore,
    QuestionBankSyncRequestTokenConflictError,
    QuestionBankSyncSessionBusyError,
)
from db_manager import DBManager
from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_write_service import QuestionBankWriteService


def _request_payload(
    *,
    token: str = "a" * 32,
    revision: str = "b" * 64,
    source_sha256: str = "c" * 64,
) -> dict[str, object]:
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "session_id": 7,
                "mode": "sync",
                "config_revision": revision,
                "source_paper_sha256": source_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "session_id": 7,
        "mode": "sync",
        "config_revision": revision,
        "source_paper_sha256": source_sha256,
        "client_request_token": token,
        "client_request_fingerprint": fingerprint,
    }


def test_question_bank_sync_request_is_idempotent_and_token_bound(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "grading.db")
    payload = _request_payload()

    first, created = store.create_idempotent_question_bank_sync_job(payload)
    repeated, repeated_created = store.create_idempotent_question_bank_sync_job(payload)
    same_version, same_version_created = (
        store.create_idempotent_question_bank_sync_job(
            _request_payload(token="e" * 32)
        )
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert same_version_created is False
    assert same_version.id == first.id
    with pytest.raises(QuestionBankSyncRequestTokenConflictError):
        store.create_idempotent_question_bank_sync_job(
            _request_payload(revision="d" * 64)
        )


def test_question_bank_sync_rejects_a_second_active_request_for_the_session(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "grading.db")
    store.create_idempotent_question_bank_sync_job(_request_payload())

    with pytest.raises(QuestionBankSyncSessionBusyError):
        store.create_idempotent_question_bank_sync_job(
            _request_payload(token="e" * 32, revision="d" * 64)
        )


def test_config_publish_invalidates_ready_sync_when_only_config_changes(
    tmp_path: Path,
) -> None:
    db, session_id, _source, _source_sha256, revision = _configured_session(
        tmp_path
    )
    session = db.get_grading_session(session_id)
    assert session is not None
    rubric_path = Path(str(session["rubric_path"]))
    answer_path = Path(str(session["answer_key_path"]))
    replacement_rubric = rubric_path.with_name("rubric-replacement.json")
    replacement_answer = answer_path.with_name("answer-replacement.json")
    replacement_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    replacement_payload["total_score"] = 99
    replacement_rubric.write_text(
        json.dumps(replacement_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    replacement_answer.write_bytes(answer_path.read_bytes())
    db.update_question_bank_sync_state(
        session_id,
        state="ready",
        details={
            "config_revision": revision,
            "source_paper_sha256": session["source_paper_sha256"],
        },
        error="old sync error",
    )
    _mark_template_snapshot_pending(db, session_id)

    store = JobStore(db.db_path)
    assert store.update_session_config_if_idle(
        session_id,
        rubric_path=str(replacement_rubric),
        answer_key_path=str(replacement_answer),
        expected_rubric_path=str(rubric_path),
        expected_answer_key_path=str(answer_path),
    )

    changed = db.get_grading_session(session_id)
    assert changed is not None
    assert load_editor_config(db, session_id).revision != revision
    assert changed["source_paper_sha256"] == session["source_paper_sha256"]
    assert changed["question_bank_sync_state"] == "not_started"
    assert json.loads(changed["question_bank_sync_details_json"]) == {}
    assert changed["question_bank_sync_error"] is None
    assert changed["question_bank_sync_updated_at"] is None
    _assert_template_snapshot_invalidated(db, session_id)


def test_generated_config_bind_invalidates_ready_sync_for_same_source(
    tmp_path: Path,
) -> None:
    db, session_id, source, source_sha256, revision = _configured_session(tmp_path)
    session = db.get_grading_session(session_id)
    assert session is not None
    old_rubric_path = Path(str(session["rubric_path"]))
    old_answer_path = Path(str(session["answer_key_path"]))
    replacement_rubric = old_rubric_path.with_name("generated-rubric.json")
    replacement_answer = old_answer_path.with_name("generated-answer.json")
    replacement_payload = json.loads(old_rubric_path.read_text(encoding="utf-8"))
    replacement_payload["total_score"] = 98
    replacement_rubric.write_text(
        json.dumps(replacement_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    replacement_answer.write_bytes(old_answer_path.read_bytes())
    db.update_question_bank_sync_state(
        session_id,
        state="ready",
        details={
            "config_revision": revision,
            "source_paper_sha256": source_sha256,
        },
    )
    _mark_template_snapshot_pending(db, session_id)
    store = JobStore(db.db_path)
    job = store.create_job("config_generation", {"session_id": session_id})
    assert store.mark_running(job.id)

    assert store.finish_config_generation_and_bind(
        job.id,
        session_id=session_id,
        expected_rubric_path=str(old_rubric_path),
        expected_answer_key_path=str(old_answer_path),
        rubric_path=str(replacement_rubric),
        answer_key_path=str(replacement_answer),
        source_paper_path=str(source),
        source_paper_sha256=source_sha256,
        result={"outcome": "complete"},
    )

    changed = db.get_grading_session(session_id)
    assert changed is not None
    assert load_editor_config(db, session_id).revision != revision
    assert changed["source_paper_sha256"] == source_sha256
    assert changed["question_bank_sync_state"] == "not_started"
    assert json.loads(changed["question_bank_sync_details_json"]) == {}
    assert changed["question_bank_sync_error"] is None
    assert changed["question_bank_sync_updated_at"] is None
    _assert_template_snapshot_invalidated(db, session_id)


def test_stale_job_cannot_overwrite_a_newer_sync_owner(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, _revision = _configured_session(
        tmp_path
    )
    store = JobStore(db.db_path)
    newer_revision = "d" * 64
    db.update_question_bank_sync_state(
        session_id,
        state="running",
        details={
            "config_revision": newer_revision,
            "job_id": 99,
            "source_paper_sha256": source_sha256,
            "stage": "tagging",
        },
    )

    assert store.transition_question_bank_sync_state_if_owned(
        session_id=session_id,
        job_id=98,
        source_paper_sha256=source_sha256,
        config_revision="e" * 64,
        state="not_started",
        details={"stage": "stale"},
    ) is False

    unchanged = db.get_grading_session(session_id)
    assert unchanged is not None
    assert unchanged["question_bank_sync_state"] == "running"
    assert json.loads(unchanged["question_bank_sync_details_json"]) == {
        "config_revision": newer_revision,
        "job_id": 99,
        "source_paper_sha256": source_sha256,
        "stage": "tagging",
    }


def test_stale_job_cannot_claim_running_after_config_binding_changes(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, revision = _configured_session(
        tmp_path
    )
    session = db.get_grading_session(session_id)
    assert session is not None
    old_rubric = str(session["rubric_path"])
    old_answer = str(session["answer_key_path"])
    store = JobStore(db.db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "source_paper_sha256": source_sha256,
            "config_revision": revision,
        },
    )
    assert store.mark_running(job.id)
    replacement_rubric = str(Path(old_rubric).with_name("new-rubric.json"))
    replacement_answer = str(Path(old_answer).with_name("new-answer.json"))
    assert db.publish_grading_session_config(
        session_id,
        rubric_path=replacement_rubric,
        answer_key_path=replacement_answer,
        expected_rubric_path=old_rubric,
        expected_answer_key_path=old_answer,
    )

    assert store.claim_question_bank_sync_state_if_current(
        session_id=session_id,
        job_id=job.id,
        source_paper_sha256=source_sha256,
        config_revision=revision,
        expected_rubric_path=old_rubric,
        expected_answer_key_path=old_answer,
        details={"stage": "importing", "mode": "sync"},
    ) is False
    current = db.get_grading_session(session_id)
    assert current is not None
    assert current["question_bank_sync_state"] == "not_started"


def _configured_session(
    tmp_path: Path,
) -> tuple[DBManager, int, Path, str, str]:
    data_root = tmp_path / "data"
    databases = data_root / "databases"
    databases.mkdir(parents=True)
    db = DBManager(databases / "grading.db")
    db.initialize()
    rubric_path = data_root / "config" / "rubric.json"
    answer_path = data_root / "config" / "answer.json"
    rubric_path.parent.mkdir(parents=True)
    rubric_path.write_text(
        json.dumps(
            {
                "total_score": 100,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "max_score": 100,
                        "parts": [
                            {
                                "part_id": "Q1",
                                "part_score": 100,
                                "response_mode": "exact_objective",
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "step_score": 100,
                                        "core_goal": "选择正确选项",
                                        "required_elements": ["B"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "canonical_answer": "B",
                        "parts": [{"part_id": "Q1", "answer": "B"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source = data_root / "question_bank" / "raw_papers" / "paper.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"controlled source paper")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    session_id = db.create_grading_session(
        "同步测试",
        str(rubric_path),
        str(answer_path),
        source_paper_path=str(source),
        source_paper_sha256=source_sha256,
    )
    revision = load_editor_config(db, session_id).revision
    return db, session_id, source, source_sha256, revision


def _mark_template_snapshot_pending(db: DBManager, session_id: int) -> None:
    template_id = db.upsert_session_template(
        session_id,
        "controlled-front.png",
        "controlled-back.png",
    )
    token = db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [],
        confirmed=True,
    )
    template = db.get_session_template(session_id)
    assert template is not None
    assert template["is_confirmed"] == 1
    assert template["regions_snapshot_pending"] == 1
    assert template["regions_snapshot_token"] == token


def _assert_template_snapshot_invalidated(
    db: DBManager,
    session_id: int,
) -> None:
    template = db.get_session_template(session_id)
    assert template is not None
    assert template["is_confirmed"] == 0
    assert template["regions_snapshot_pending"] == 0
    assert template["regions_snapshot_token"] is None


def test_sync_runs_import_then_governed_tagging_and_links_without_touching_config(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, revision = _configured_session(
        tmp_path
    )
    question_bank_db = tmp_path / "data" / "databases" / "question_bank.db"
    initialize_database(question_bank_db)
    store = JobStore(db.db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "mode": "sync",
            "config_revision": revision,
            "source_paper_sha256": source_sha256,
            "client_request_token": "f" * 32,
            "client_request_fingerprint": "1" * 64,
        },
    )
    assert store.mark_running(job.id)
    context = JobContext(
        job_id=job.id,
        job_type=job.job_type,
        payload=job.payload,
        store=store,
    )
    call_order: list[str] = []
    observed_governance: list[Any] = []
    rubric_before = Path(db.get_grading_session(session_id)["rubric_path"]).read_bytes()

    def import_runner(**kwargs: Any) -> dict[str, object]:
        call_order.append("import")
        with connect(question_bank_db) as conn:
            cursor = conn.execute(
                """
                INSERT INTO questions (
                    question_number, question_type, question_text, answer_text,
                    source_file
                ) VALUES ('1', 'choice', '1 + 1 = ?', 'B', ?)
                """,
                (str(db.get_grading_session(session_id)["source_paper_path"]),),
            )
            question_id = int(cursor.lastrowid)
        return {
            "outcome": "complete",
            "successful_question_ids": [question_id],
            "failed_question_ids": [],
            "failed_count": 0,
            "retryable": False,
        }

    def tagging_runner(**kwargs: Any) -> dict[str, object]:
        call_order.append("tag")
        observed_governance.append(kwargs.get("taxonomy_governance"))
        ids = list(kwargs["context"].payload["question_ids"])
        return {
            "outcome": "complete",
            "requested_count": len(ids),
            "tagged_count": len(ids),
            "successful_question_ids": ids,
            "failed_question_ids": [],
            "failed_count": 0,
            "review_count": 1,
            "proposal_ids": ["proposal-1"],
            "retryable": False,
        }

    governance = object()
    result = run_session_question_bank_sync_job(
        context=context,
        grading_db=db,
        question_bank_db_path=question_bank_db,
        data_root=tmp_path / "data",
        write_service=QuestionBankWriteService(
            question_bank_db,
            data_root=tmp_path / "data",
        ),
        question_import_runner=import_runner,
        tagging_sync_runner=tagging_runner,
        ai_service_factory=lambda: object(),
        taxonomy_governance=governance,
    )

    assert call_order == ["import", "tag"]
    assert observed_governance == [governance]
    assert result["outcome"] == "complete"
    assert result["review_count"] == 1
    assert db.get_grading_session(session_id)["question_bank_sync_state"] == "ready"
    assert Path(db.get_grading_session(session_id)["rubric_path"]).read_bytes() == rubric_before


def test_tag_retry_skips_import_and_failure_stays_in_the_sync_state(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, revision = _configured_session(
        tmp_path
    )
    question_bank_db = tmp_path / "data" / "databases" / "question_bank.db"
    initialize_database(question_bank_db)
    with connect(question_bank_db) as conn:
        conn.execute(
            """
            INSERT INTO questions (
                id, question_number, question_type, question_text, answer_text
            ) VALUES (201, '1', 'choice', '1 + 1 = ?', 'B')
            """
        )
    store = JobStore(db.db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "mode": "tag_retry",
            "question_ids": [201],
            "config_revision": revision,
            "source_paper_sha256": source_sha256,
            "client_request_token": "2" * 32,
            "client_request_fingerprint": "3" * 64,
            "retry_of_job_id": 19,
        },
    )
    assert store.mark_running(job.id)
    context = JobContext(
        job_id=job.id,
        job_type=job.job_type,
        payload=job.payload,
        store=store,
    )
    import_called = False

    def unexpected_import(**_kwargs: Any) -> dict[str, object]:
        nonlocal import_called
        import_called = True
        raise AssertionError("tag retry must not import again")

    result = run_session_question_bank_sync_job(
        context=context,
        grading_db=db,
        question_bank_db_path=question_bank_db,
        data_root=tmp_path / "data",
        write_service=QuestionBankWriteService(
            question_bank_db,
            data_root=tmp_path / "data",
        ),
        question_import_runner=unexpected_import,
        tagging_sync_runner=lambda **_kwargs: {
            "outcome": "failed",
            "requested_count": 1,
            "tagged_count": 0,
            "successful_question_ids": [],
            "failed_question_ids": [201],
            "failed_count": 1,
            "review_count": 0,
            "proposal_ids": [],
            "retryable": True,
        },
        ai_service_factory=lambda: object(),
        taxonomy_governance=object(),
    )

    assert import_called is False
    assert result["outcome"] == "failed"
    assert result["retryable"] is True
    assert db.get_grading_session(session_id)["question_bank_sync_state"] == "failed"
    assert db.get_grading_session(session_id)["rubric_path"]


def test_cancellation_remains_cancelled_and_leaves_a_recoverable_sync_state(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, revision = _configured_session(
        tmp_path
    )
    question_bank_db = tmp_path / "data" / "databases" / "question_bank.db"
    initialize_database(question_bank_db)
    store = JobStore(db.db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "mode": "sync",
            "config_revision": revision,
            "source_paper_sha256": source_sha256,
            "client_request_token": "4" * 32,
            "client_request_fingerprint": "5" * 64,
        },
    )
    assert store.mark_running(job.id)
    assert store.request_cancel(job.id)
    context = JobContext(
        job_id=job.id,
        job_type=job.job_type,
        payload=job.payload,
        store=store,
    )

    with pytest.raises(JobCancellationRequested):
        run_session_question_bank_sync_job(
            context=context,
            grading_db=db,
            question_bank_db_path=question_bank_db,
            data_root=tmp_path / "data",
            write_service=QuestionBankWriteService(
                question_bank_db,
                data_root=tmp_path / "data",
            ),
            question_import_runner=lambda **_kwargs: pytest.fail(
                "cancelled work must not start importing"
            ),
            tagging_sync_runner=lambda **_kwargs: pytest.fail(
                "cancelled work must not start tagging"
            ),
            ai_service_factory=lambda: object(),
            taxonomy_governance=object(),
        )

    session = db.get_grading_session(session_id)
    assert session["question_bank_sync_state"] == "partial"
    details = json.loads(session["question_bank_sync_details_json"])
    assert details == {
        "config_revision": revision,
        "job_id": job.id,
        "retryable": True,
        "source_paper_sha256": source_sha256,
        "stage": "cancelled",
    }


def test_stale_sync_finishes_failed_and_cannot_leave_session_running(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, revision = _configured_session(
        tmp_path
    )
    session = db.get_grading_session(session_id)
    assert session is not None
    rubric_path = Path(str(session["rubric_path"]))
    question_bank_db = tmp_path / "data" / "databases" / "question_bank.db"
    store = JobStore(db.db_path)
    manager = JobManager(store, max_workers=1, cleanup_interrupted=False)
    write_service = SimpleNamespace(
        stage_upload=lambda **_kwargs: SimpleNamespace(upload_id="upload-1"),
        create_import_request=lambda **_kwargs: SimpleNamespace(request_id="request-1"),
    )

    def change_config_during_import(**_kwargs: Any) -> dict[str, object]:
        payload = json.loads(rubric_path.read_text(encoding="utf-8"))
        payload["total_score"] = 97
        rubric_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "outcome": "complete",
            "successful_question_ids": [],
            "failed_question_ids": [],
            "failed_count": 0,
            "retryable": False,
        }

    manager.register(
        "question_bank_sync",
        lambda context: run_session_question_bank_sync_job(
            context=context,
            grading_db=db,
            question_bank_db_path=question_bank_db,
            data_root=tmp_path / "data",
            write_service=write_service,
            question_import_runner=change_config_during_import,
            tagging_sync_runner=lambda **_kwargs: pytest.fail(
                "stale work must stop before tagging"
            ),
            ai_service_factory=lambda: object(),
            taxonomy_governance=object(),
        ),
    )
    payload = {
        "session_id": session_id,
        "mode": "sync",
        "config_revision": revision,
        "source_paper_sha256": source_sha256,
        "client_request_token": "6" * 32,
        "client_request_fingerprint": "7" * 64,
    }

    try:
        job, created = manager.submit_idempotent_question_bank_sync(payload)
        assert created is True
        manager.wait(job.id, timeout=5)
        completed = store.get_job(job.id)
        assert completed is not None
        assert completed.status == "failed"
        assert completed.error == "grading configuration changed"
    finally:
        manager.shutdown()

    changed = db.get_grading_session(session_id)
    assert changed is not None
    assert changed["question_bank_sync_state"] == "not_started"
    details = json.loads(changed["question_bank_sync_details_json"])
    assert details == {
        "config_revision": revision,
        "job_id": job.id,
        "reason": "grading_configuration_changed",
        "retryable": False,
        "source_paper_sha256": source_sha256,
        "stage": "stale",
    }
