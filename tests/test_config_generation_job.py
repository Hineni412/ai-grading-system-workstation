from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.jobs.config_generation import (
    load_config_generation_input,
    run_config_generation_job,
    stage_config_generation_input,
)
from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager
from session_manager import save_generated_config


def _minimal_input() -> dict[str, object]:
    return {
        "confirmed_blocks": [
            {
                "question_id": "Q1",
                "question_type": "choice",
                "text": "1 + 1 = ?",
                "canonical_answer": "2",
            }
        ],
        "document_text": "1. 1 + 1 = ?\n答案：2",
        "question_images": {},
    }


def _valid_config_payload() -> dict[str, object]:
    scores = [17, 17, 17, 17, 17, 15]
    rubric_questions = []
    answer_questions = []
    for index, score in enumerate(scores, start=1):
        question_id = f"Q{index}"
        part_id = f"{question_id}-P1"
        rubric_questions.append(
            {
                "question_id": question_id,
                "question_type": "comprehensive",
                "max_score": score,
                "knowledge_id": f"K{index}",
                "parts": [
                    {
                        "part_id": part_id,
                        "part_score": score,
                        "steps": [
                            {
                                "step_id": f"{part_id}-S1",
                                "step_score": score,
                                "core_goal": "answer",
                                "required_elements": [str(index)],
                                "allow_alternative_methods": True,
                            }
                        ],
                    }
                ],
            }
        )
        answer_questions.append(
            {
                "question_id": question_id,
                "canonical_answer": str(index),
                "accepted_forms": [str(index)],
                "method_variants": [],
                "parts": [
                    {
                        "part_id": part_id,
                        "answer": str(index),
                        "analysis": "",
                        "step_milestones": [str(index)],
                    }
                ],
            }
        )
    return {
        "rubric": {
            "exam_title": "Atomic Exam",
            "total_score": 100,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {"warnings": []},
    }


def test_stage_config_generation_input_round_trips_without_client_path(
    tmp_path: Path,
) -> None:
    input_id = stage_config_generation_input(tmp_path, session_id=7, **_minimal_input())

    assert len(input_id) == 32
    assert load_config_generation_input(tmp_path, input_id) == {
        **_minimal_input(),
        "session_id": 7,
    }
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        f"config_generation_input_{input_id}.json"
    ]


def test_load_config_generation_input_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid config generation input id"):
        load_config_generation_input(tmp_path, "../outside")


def test_stage_config_generation_input_cleans_temp_file_when_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("publish failed")

    monkeypatch.setattr("backend.jobs.config_generation.os.replace", fail_replace)

    with pytest.raises(OSError, match="publish failed"):
        stage_config_generation_input(tmp_path, session_id=7, **_minimal_input())

    assert list(tmp_path.iterdir()) == []


def test_save_generated_config_uses_atomic_replace_for_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacements: list[tuple[Path, Path]] = []
    real_replace = __import__("os").replace

    def record_replace(source: object, target: object) -> None:
        source_path = Path(source)
        target_path = Path(target)
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr("session_manager.os.replace", record_replace)

    rubric_path, answer_path = save_generated_config(
        tmp_path,
        _valid_config_payload(),
        "atomic",
    )

    assert [target for _source, target in replacements] == [rubric_path, answer_path]
    assert all(source.parent == target.parent for source, target in replacements)
    assert json.loads(rubric_path.read_text(encoding="utf-8"))["exam_title"] == "Atomic Exam"
    assert json.loads(answer_path.read_text(encoding="utf-8"))["questions"][0]["question_id"] == "Q1"
    assert not any(path.name.startswith(".") for path in tmp_path.iterdir())


def _job_context(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[JobContext, JobStore]:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("config_generation", payload)
    assert store.mark_running(job.id)
    return (
        JobContext(
            job_id=job.id,
            job_type=job.job_type,
            payload=job.payload,
            store=store,
        ),
        store,
    )


def _db_with_session(tmp_path: Path) -> tuple[DBManager, int, tuple[str, str]]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    initial_dir = tmp_path / "initial"
    rubric_path, answer_path = save_generated_config(
        initial_dir,
        _valid_config_payload(),
        "initial",
    )
    session_id = db.create_grading_session(
        "Config Job Exam",
        str(rubric_path),
        str(answer_path),
    )
    return db, session_id, (str(rubric_path), str(answer_path))


def _stage_job_input(tmp_path: Path, session_id: int) -> str:
    return stage_config_generation_input(
        tmp_path / "uploaded",
        session_id=session_id,
        **_minimal_input(),
    )


def test_config_generation_job_binds_complete_result_and_returns_safe_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id)
    context, _store = _job_context(
        tmp_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    fake_client = object()
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=lambda: fake_client,
    )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert (session["rubric_path"], session["answer_key_path"]) != old_paths
    assert Path(session["rubric_path"]).is_file()
    assert Path(session["answer_key_path"]).is_file()
    assert result == {
        "session_id": session_id,
        "outcome": "complete",
        "total_questions": 6,
        "generated_questions": 6,
        "failed_count": 0,
        "failed_question_ids": [],
        "retryable": False,
    }
    assert str(tmp_path) not in json.dumps(result)


def test_config_generation_job_saves_partial_draft_without_binding_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id)
    context, _store = _job_context(
        tmp_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    partial = _valid_config_payload()
    partial["meta"] = {
        "warnings": ["Q2 generation failed"],
        "failed_question_ids": ["Q2"],
        "failed_questions": [
            {
                "question_id": "Q2",
                "attempts": 1,
                "category": "transient_network",
                "error": "temporary upstream",
            }
        ],
        "score_allocation_pending": True,
    }
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: partial,
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=lambda: object(),
    )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert (session["rubric_path"], session["answer_key_path"]) == old_paths
    assert result["outcome"] == "partial"
    assert result["failed_question_ids"] == ["Q2"]
    assert result["retryable"] is True
    draft = tmp_path / "uploaded" / f"config_generation_draft_job_{context.job_id}.json"
    assert json.loads(draft.read_text(encoding="utf-8"))["meta"]["failed_question_ids"] == ["Q2"]


def test_config_generation_job_honours_cancellation_before_final_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id)
    context, store = _job_context(
        tmp_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )

    def cancel_during_generation(*_args: object, **kwargs: object) -> dict[str, object]:
        assert store.request_cancel(context.job_id)
        report = kwargs["report"]
        assert callable(report)
        report(0.8, "generation", "model returned")
        return _valid_config_payload()

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        cancel_during_generation,
    )

    with pytest.raises(JobCancellationRequested):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert (session["rubric_path"], session["answer_key_path"]) == old_paths
    assert not list((tmp_path / "uploaded").glob("rubric_job-*.json"))
    assert not list((tmp_path / "uploaded").glob("answer_key_job-*.json"))


def test_default_handlers_register_config_generation_with_controlled_dependencies(
    tmp_path: Path,
) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers

    captured: dict[str, object] = {}

    def fake_runner(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        context = kwargs["context"]
        assert isinstance(context, JobContext)
        return {"session_id": context.payload["session_id"], "outcome": "complete"}

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        upload_config_dir=tmp_path / "uploaded",
        config_generation_runner=fake_runner,
        llm_client_factory=lambda: "fake-client",
    )
    try:
        job = manager.submit(
            "config_generation",
            {"session_id": 7, "mode": "generate", "input_id": "a" * 32},
        )
        manager.wait(job.id, timeout=5)
    finally:
        manager.shutdown()

    loaded = manager.get(job.id)
    assert loaded is not None
    assert loaded.status == "succeeded"
    assert loaded.result == {"session_id": 7, "outcome": "complete"}
    assert isinstance(captured["db"], DBManager)
    assert captured["upload_config_dir"] == tmp_path / "uploaded"
    assert captured["llm_client_factory"]() == "fake-client"


def test_config_generation_retry_loads_source_draft_and_selected_questions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    upload_dir = tmp_path / "uploaded"
    input_id = _stage_job_input(tmp_path, session_id)
    store = JobStore(tmp_path / "jobs.db")
    source = store.create_job(
        "config_generation",
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": ["Q2"],
            "retryable": True,
        },
    )
    partial = _valid_config_payload()
    partial["meta"] = {
        "warnings": ["Q2 generation failed"],
        "failed_question_ids": ["Q2"],
        "failed_questions": [{"question_id": "Q2", "error": "temporary"}],
    }
    draft = upload_dir / f"config_generation_draft_job_{source.id}.json"
    draft.write_text(json.dumps(partial, ensure_ascii=False), encoding="utf-8")
    retry = store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "retry",
            "source_job_id": source.id,
            "retry_question_ids": ["Q2"],
        },
    )
    assert store.mark_running(retry.id)
    context = JobContext(
        job_id=retry.id,
        job_type=retry.job_type,
        payload=retry.payload,
        store=store,
    )
    captured: dict[str, object] = {}

    def fake_retry(*args: object, **kwargs: object) -> dict[str, object]:
        captured["existing"] = args[0]
        captured["blocks"] = args[1]
        captured["retry_question_ids"] = kwargs["retry_question_ids"]
        return _valid_config_payload()

    monkeypatch.setattr(
        "backend.jobs.config_generation.retry_failed_grading_config_questions",
        fake_retry,
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=upload_dir,
        llm_client_factory=lambda: object(),
    )

    assert captured["existing"] == partial
    assert captured["retry_question_ids"] == ["Q2"]
    assert result["outcome"] == "complete"
    session = db.get_grading_session(session_id)
    assert session is not None
    assert (session["rubric_path"], session["answer_key_path"]) != old_paths


def test_config_generation_job_rejects_input_staged_for_another_session(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id + 1)
    context, _store = _job_context(
        tmp_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )

    with pytest.raises(ValueError, match="does not belong to session"):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert (session["rubric_path"], session["answer_key_path"]) == old_paths
