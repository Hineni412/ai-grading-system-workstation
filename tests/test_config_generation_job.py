from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fitz
import pytest
from docx import Document

from backend.config_workspace.sources import (
    ConfigSourceChangedError,
    ConfigSourceRecord,
    ConfigSourceService,
    QuestionDecision,
)
from backend.jobs.config_generation import (
    _config_analysis_images,
    _run_config_generation_job_impl,
    cleanup_consumed_config_retry_artifacts,
    discard_config_generation_input,
    load_config_generation_input,
    preserve_interrupted_config_generation_checkpoints,
    run_config_generation_job,
    stage_config_generation_input,
    stage_config_refine_input,
    stage_config_source_generation_input,
    _submit_automatic_question_bank_sync,
)
from backend.config_workspace.editor import (
    ManualPartInput,
    ReplaceScoringUnitsCommand,
    apply_config_editor_changes,
    editor_part_ids,
)
from backend.config_workspace.publish import load_editor_config
from backend.config_workspace.deferred_analysis import DeferredAnalysisArtifactStore
from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.jobs.store import ConfigRetryAlreadySubmittedError, ConfigSessionBusyError
from db_manager import DBManager
from question_id_contract import canonicalize_grading_config_payload
from question_bank.services.source_paper_archive_service import archive_source_bytes
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
                        "response_mode": "short_answer_points",
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


async def _chunks(content: bytes):
    yield content


def _docx_bytes(text: str = "1. Prove x equals x.\nAnswer: proven") -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Synthetic exam\n1. Compute 1 + 1.\nAnswer\n1. 2",
        fontsize=12,
    )
    content = document.tobytes()
    document.close()
    return content


def _controlled_source(
    tmp_path: Path,
    session_id: int,
    *,
    suffix: str = ".docx",
    text: str = "1. Prove x equals x.\nAnswer: proven",
) -> tuple[ConfigSourceService, ConfigSourceRecord]:
    service = ConfigSourceService(tmp_path / "uploaded")
    content = _pdf_bytes() if suffix == ".pdf" else _docx_bytes(text)
    record = asyncio.run(
        service.stage_and_parse(
            session_id=session_id,
            filename=f"七年级下册测试卷{suffix}",
            chunks=_chunks(content),
        )
    )
    return service, record


def _stage_controlled_input(
    tmp_path: Path,
    source_service: ConfigSourceService,
    source: ConfigSourceRecord,
    expected_paths: tuple[str, str],
    *,
    generation_mode: str,
    sync_to_question_bank: bool = False,
) -> str:
    return stage_config_source_generation_input(
        tmp_path / "uploaded",
        session_id=source.session_id,
        expected_rubric_path=expected_paths[0],
        expected_answer_key_path=expected_paths[1],
        generation_mode=generation_mode,
        source_id=source.source_id,
        source_revision=source.source_revision,
        sync_to_question_bank=sync_to_question_bank,
        curriculum_volume_id=(
            "bnu24-math-g7-upper" if sync_to_question_bank else None
        ),
        decisions=(
            []
            if generation_mode == "whole_document"
            else [
                {
                    "question_id": source.questions[0].question_id,
                    "question_type": "proof",
                    "excluded": False,
                }
            ]
        ),
    )


def _two_same_sha_source_jobs(
    tmp_path: Path,
) -> tuple[
    DBManager,
    list[tuple[int, tuple[str, str], ConfigSourceRecord, JobContext]],
]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    source_bytes = _docx_bytes("1. Prove shared equals shared.\nAnswer: proven")
    jobs: list[tuple[int, tuple[str, str], ConfigSourceRecord, JobContext]] = []
    source_service = ConfigSourceService(tmp_path / "uploaded")
    for index in range(2):
        rubric_path, answer_path = save_generated_config(
            tmp_path / f"initial-{index}",
            _valid_config_payload(),
            f"initial-{index}",
        )
        old_paths = (str(rubric_path), str(answer_path))
        session_id = db.create_grading_session(
            f"Concurrent Config Job {index}",
            old_paths[0],
            old_paths[1],
        )
        source = asyncio.run(
            source_service.stage_and_parse(
                session_id=session_id,
                filename="shared.docx",
                chunks=_chunks(source_bytes),
            )
        )
        input_id = _stage_controlled_input(
            tmp_path,
            source_service,
            source,
            old_paths,
            generation_mode="per_question",
        )
        context, _store = _job_context(
            db.db_path,
            {
                "session_id": session_id,
                "mode": "generate",
                "generation_mode": "per_question",
                "input_id": input_id,
                "source_id": source.source_id,
                "source_revision": source.source_revision,
            },
        )
        jobs.append((session_id, old_paths, source, context))
    assert jobs[0][2].sha256 == jobs[1][2].sha256
    return db, jobs


def _force_concurrent_archive_reuse_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.services import source_paper_archive_service as archive_module

    real_reuse = archive_module._reuse_matching
    barrier = threading.Barrier(2)

    def synchronized_reuse(*args: object, **kwargs: object):
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            return real_reuse(*args, **kwargs)
        return None

    monkeypatch.setattr(archive_module, "_reuse_matching", synchronized_reuse)


def test_stage_config_generation_input_round_trips_without_client_path(
    tmp_path: Path,
) -> None:
    input_id = stage_config_generation_input(
        tmp_path,
        session_id=7,
        expected_rubric_path="server-rubric.json",
        expected_answer_key_path="server-answer.json",
        **_minimal_input(),
    )

    assert len(input_id) == 32
    assert load_config_generation_input(tmp_path, input_id) == {
        **_minimal_input(),
        "session_id": 7,
        "expected_rubric_path": "server-rubric.json",
        "expected_answer_key_path": "server-answer.json",
        "sync_to_question_bank": False,
    }
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        f"config_generation_input_{input_id}.json"
    ]


def test_automatic_question_bank_sync_uses_published_revision_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[dict[str, object]] = []
    context = SimpleNamespace(
        job_id=41,
        submit_question_bank_sync=lambda payload: (
            submitted.append(payload)
            or SimpleNamespace(id=73, status="queued"),
            True,
        ),
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.load_editor_config",
        lambda _db, _session_id: SimpleNamespace(
            configured=True,
            revision="a" * 64,
            session={},
        ),
    )
    summary: dict[str, object] = {}

    _submit_automatic_question_bank_sync(
        context=context,
        db=object(),
        session_id=7,
        source_paper_sha256="b" * 64,
        summary=summary,
        source_safe_filename="七年级下册测试卷.docx",
    )

    assert len(submitted) == 1
    request = submitted[0]
    assert request["session_id"] == 7
    assert request["config_revision"] == "a" * 64
    assert request["source_paper_sha256"] == "b" * 64
    assert len(str(request["client_request_token"])) == 32
    assert len(str(request["client_request_fingerprint"])) == 64
    assert summary["question_bank_sync_state"] == "queued"
    assert summary["question_bank_sync_job_id"] == 73


def test_load_config_generation_input_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid config generation input id"):
        load_config_generation_input(tmp_path, "../outside")


def test_load_config_generation_input_enforces_serialized_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.jobs.config_generation as generation_module

    input_id = stage_config_generation_input(
        tmp_path,
        session_id=7,
        expected_rubric_path="server-rubric.json",
        expected_answer_key_path="server-answer.json",
        **_minimal_input(),
    )
    monkeypatch.setattr(generation_module, "MAX_CONFIG_GENERATION_INPUT_BYTES", 8)
    with pytest.raises(Exception, match="secure file exceeds size limit"):
        load_config_generation_input(tmp_path, input_id)


def test_generation_input_io_uses_secure_root_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.config_workspace.secure_fs import SecureRootFilesystem

    writes: list[Path] = []
    reads: list[Path] = []
    deletes: list[tuple[Path, ...]] = []
    real_write = SecureRootFilesystem.write_json_atomic
    real_read = SecureRootFilesystem.read_text
    real_unlink = SecureRootFilesystem.unlink_many

    def tracked_write(self, path, payload):
        writes.append(Path(path))
        return real_write(self, path, payload)

    def tracked_read(self, path, *, encoding="utf-8", max_bytes=None):
        reads.append(Path(path))
        return real_read(self, path, encoding=encoding, max_bytes=max_bytes)

    def tracked_unlink(self, paths):
        owned = tuple(Path(path) for path in paths)
        deletes.append(owned)
        return real_unlink(self, owned)

    monkeypatch.setattr(SecureRootFilesystem, "write_json_atomic", tracked_write)
    monkeypatch.setattr(SecureRootFilesystem, "read_text", tracked_read)
    monkeypatch.setattr(SecureRootFilesystem, "unlink_many", tracked_unlink)

    input_id = stage_config_generation_input(
        tmp_path,
        session_id=7,
        expected_rubric_path="server-rubric.json",
        expected_answer_key_path="server-answer.json",
        **_minimal_input(),
    )
    load_config_generation_input(tmp_path, input_id)
    discard_config_generation_input(tmp_path, input_id)

    expected = tmp_path / f"config_generation_input_{input_id}.json"
    assert writes == [expected]
    assert reads == [expected]
    assert deletes == [(expected,)]


def test_stage_config_generation_input_cleans_temp_file_when_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.config_workspace.secure_fs import SecureRootFilesystem

    def fail_write(_self: object, _path: object, _payload: object) -> None:
        raise OSError("publish failed")

    monkeypatch.setattr(SecureRootFilesystem, "write_json_atomic", fail_write)

    with pytest.raises(OSError, match="publish failed"):
        stage_config_generation_input(
            tmp_path,
            session_id=7,
            expected_rubric_path="server-rubric.json",
            expected_answer_key_path="server-answer.json",
            **_minimal_input(),
        )

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
    job_db_path: Path,
    payload: dict[str, object],
) -> tuple[JobContext, JobStore]:
    store = JobStore(job_db_path)
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


def _stage_job_input(
    tmp_path: Path,
    session_id: int,
    expected_paths: tuple[str, str],
    *,
    question_ids: list[str] | None = None,
) -> str:
    staged = _minimal_input()
    if question_ids is not None:
        staged["confirmed_blocks"] = [
            {
                "question_id": question_id,
                "question_type": "comprehensive",
                "text": f"{question_id} controlled question",
                "canonical_answer": question_id,
            }
            for question_id in question_ids
        ]
    return stage_config_generation_input(
        tmp_path / "uploaded",
        session_id=session_id,
        expected_rubric_path=expected_paths[0],
        expected_answer_key_path=expected_paths[1],
        **staged,
    )


def test_config_generation_job_binds_complete_result_and_returns_safe_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(
        tmp_path,
        session_id,
        old_paths,
        question_ids=[f"Q{index}" for index in range(1, 7)],
    )
    context, _store = _job_context(
        db.db_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    fake_client = object()
    generated_payload = _valid_config_payload()
    generated_payload["meta"] = {
        "batches": [
            {
                "batch_id": "B001",
                "question_ids": ["Q1", "Q2", "Q3"],
                "status": "succeeded",
                "local_json_repair": {
                    "repaired": True,
                    "operations": ["remove_trailing_comma"],
                    "response_chars": 123,
                    "response_sha256": "a" * 64,
                },
            }
        ],
        "failed_batches": [],
    }
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: generated_payload,
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
    assert {key: result[key] for key in (
        "session_id", "outcome", "total_questions", "generated_questions",
        "failed_count", "failed_question_ids", "retryable",
    )} == {
        "session_id": session_id,
        "outcome": "complete",
        "total_questions": 6,
        "generated_questions": 6,
        "failed_count": 0,
        "failed_question_ids": [],
        "retryable": False,
    }
    assert result["mapping_status"] == "not_present"
    assert result["local_json_repairs"] == [
        {
            "batch_id": "B001",
            "question_ids": ["Q1", "Q2", "Q3"],
            "operations": ["remove_trailing_comma"],
        }
    ]
    assert isinstance(result["mapping_message"], str)
    assert str(tmp_path) not in json.dumps(result)
    stored_job = context.store.get_job(context.job_id)
    assert stored_job is not None
    assert stored_job.status == "succeeded"
    assert stored_job.result == result


def test_complete_generation_enqueues_persisted_question_bank_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="batched",
        sync_to_question_bank=True,
    )
    base_context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    submitted: list[dict[str, object]] = []
    context = JobContext(
        job_id=base_context.job_id,
        job_type=base_context.job_type,
        payload=base_context.payload,
        store=store,
        question_bank_sync_submitter=lambda payload: (
            submitted.append(payload)
            or SimpleNamespace(id=91, status="queued"),
            True,
        ),
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=lambda: object(),
    )

    assert result["outcome"] == "complete"
    assert result["question_bank_sync_requested"] is True
    assert result["question_bank_sync_state"] == "queued"
    assert result["question_bank_sync_job_id"] == 91
    assert len(submitted) == 1
    assert submitted[0]["source_paper_sha256"] == source.sha256
    assert submitted[0]["config_revision"] == result["config_revision"]
    assert submitted[0]["source_safe_filename"] == source.safe_filename


def test_complete_generation_persists_mapping_reconfirmation_when_template_files_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    template_id = db.upsert_session_template(
        session_id,
        str(tmp_path / "missing-front.png"),
        str(tmp_path / "missing-back.png"),
    )
    db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [],
        confirmed=True,
    )
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    context, _store = _job_context(
        db.db_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        mapping_output_dir=tmp_path / "mapping-output",
        llm_client_factory=lambda: object(),
    )

    assert result["mapping_status"] == "reconfirm_required"
    stored = context.store.get_job(context.job_id)
    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.result["mapping_status"] == "reconfirm_required"
    template = db.get_session_template(session_id)
    assert template["is_confirmed"] == 0
    assert template["regions_snapshot_pending"] == 0
    assert template["regions_snapshot_token"] is None
    assert str(tmp_path) not in json.dumps(stored.result, ensure_ascii=False)


def test_mapping_refresh_keeps_job_running_and_session_claimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    mapping_started = threading.Event()
    release_mapping = threading.Event()

    def blocked_mapping(*_args: object, **_kwargs: object) -> str:
        mapping_started.set()
        assert release_mapping.wait(timeout=5)
        return "refreshed"

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.refresh_template_mapping_from_session",
        blocked_mapping,
    )
    manager = JobManager(JobStore(db.db_path), max_workers=1, cleanup_interrupted=False)
    manager.register(
        "config_generation",
        lambda context: run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            mapping_output_dir=tmp_path / "mapping-output",
            llm_client_factory=lambda: object(),
        ),
    )
    try:
        job = manager.submit(
            "config_generation",
            {"session_id": session_id, "mode": "generate", "input_id": input_id},
        )
        assert mapping_started.wait(timeout=5)
        during_mapping = manager.get(job.id)
        assert during_mapping is not None
        assert during_mapping.status == "running"
        assert during_mapping.stage == "config_mapping"
        with pytest.raises(ConfigSessionBusyError):
            manager.submit(
                "config_generation",
                {"session_id": session_id, "mode": "generate", "input_id": input_id},
            )
        release_mapping.set()
        manager.wait(job.id, timeout=5)
        completed = manager.get(job.id)
        assert completed is not None
        assert completed.status == "succeeded"
        assert completed.result["mapping_status"] == "refreshed"
    finally:
        release_mapping.set()
        manager.shutdown()


@pytest.mark.parametrize(
    ("suffix", "expected_generator"),
    [(".docx", "text"), (".pdf", "images")],
)
def test_whole_document_generation_uses_one_model_call_and_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    expected_generator: str,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id, suffix=suffix)
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="whole_document",
    )
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "whole_document",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )
    calls = 0

    def client_factory() -> object:
        nonlocal calls
        calls += 1
        return object()

    generators: list[str] = []

    def from_text(*_args: object, **_kwargs: object) -> dict[str, object]:
        generators.append("text")
        return _valid_config_payload()

    def from_images(*_args: object, **_kwargs: object) -> dict[str, object]:
        generators.append("images")
        return _valid_config_payload()

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_text",
        from_text,
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_images",
        from_images,
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=client_factory,
    )

    assert calls == 1
    assert generators == [expected_generator]
    assert result["outcome"] == "complete"
    assert result["total_questions"] == 6
    session = db.get_grading_session(session_id)
    assert session is not None
    assert (session["rubric_path"], session["answer_key_path"]) != old_paths
    assert list((tmp_path / "question_bank" / "raw_papers").glob("*"))


def test_source_job_reloads_active_identity_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="per_question",
    )
    _controlled_source(tmp_path, session_id, text="1. Prove y equals y.")
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: pytest.fail("stale source must fail before model call"),
    )

    with pytest.raises(ConfigSourceChangedError):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )


def test_source_job_reloads_identity_under_lock_before_final_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="per_question",
    )
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )

    def replace_source(*_args: object, **_kwargs: object):
        _controlled_source(tmp_path, session_id, text="1. Prove z equals z.")
        return _valid_config_payload()

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        replace_source,
    )

    with pytest.raises(ConfigSourceChangedError):
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
    assert not list((tmp_path / "question_bank" / "raw_papers").glob("*"))


def test_source_generation_reuses_existing_archive_without_deleting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    archived = archive_source_bytes(
        filename=source.safe_filename,
        content=source.private_source_bytes,
        data_root=tmp_path,
    )
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="per_question",
    )
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=lambda: object(),
    )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert session["source_paper_path"] == archived.stored_path
    assert archived.physical_path.is_file()
    assert len(list(archived.physical_path.parent.glob("*"))) == 1


def test_concurrent_same_sha_source_jobs_share_one_linearized_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, jobs = _two_same_sha_source_jobs(tmp_path)
    _force_concurrent_archive_reuse_checks(monkeypatch)
    real_archive = archive_source_bytes
    archive_results: list[tuple[str, bool]] = []
    results_guard = threading.Lock()

    def record_archive(**kwargs: object):
        result = real_archive(**kwargs)
        with results_guard:
            archive_results.append((result.stored_path, result.reused))
        return result

    monkeypatch.setattr(
        "backend.jobs.config_generation.archive_source_bytes",
        record_archive,
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    def run(entry: tuple[int, tuple[str, str], ConfigSourceRecord, JobContext]):
        return run_config_generation_job(
            context=entry[3],
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, jobs))

    assert [result["outcome"] for result in results] == ["complete", "complete"]
    assert sorted(reused for _path, reused in archive_results) == [False, True]
    assert len({path for path, _reused in archive_results}) == 1
    sessions = [db.get_grading_session(entry[0]) for entry in jobs]
    assert all(session is not None for session in sessions)
    stored_paths = {str(session["source_paper_path"]) for session in sessions if session}
    assert stored_paths == {archive_results[0][0]}
    assert (tmp_path / archive_results[0][0]).is_file()


def test_concurrent_same_sha_failed_job_cannot_delete_successful_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, jobs = _two_same_sha_source_jobs(tmp_path)
    success_entry, failure_entry = jobs
    _force_concurrent_archive_reuse_checks(monkeypatch)
    real_archive = archive_source_bytes
    real_finish = JobStore.finish_config_generation_and_bind
    archive_results: list[tuple[int, str, bool]] = []
    results_guard = threading.Lock()
    both_archived = threading.Event()
    failure_finished = threading.Event()

    def record_archive(**kwargs: object):
        result = real_archive(**kwargs)
        with results_guard:
            archive_results.append(
                (threading.get_ident(), result.stored_path, result.reused)
            )
            if len(archive_results) == 2:
                both_archived.set()
        return result

    def controlled_finish(store: JobStore, job_id: int, **kwargs: object) -> bool:
        if int(job_id) == failure_entry[3].job_id:
            both_archived.wait(timeout=0.5)
            raise sqlite3.IntegrityError("injected concurrent bind failure")
        if both_archived.wait(timeout=0.1):
            assert failure_finished.wait(timeout=3)
        return real_finish(store, job_id, **kwargs)

    monkeypatch.setattr(
        "backend.jobs.config_generation.archive_source_bytes",
        record_archive,
    )
    monkeypatch.setattr(
        JobStore,
        "finish_config_generation_and_bind",
        controlled_finish,
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    def run_success() -> dict[str, object]:
        return run_config_generation_job(
            context=success_entry[3],
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
        )

    def run_failure() -> None:
        try:
            with pytest.raises(
                sqlite3.IntegrityError,
                match="injected concurrent bind failure",
            ):
                run_config_generation_job(
                    context=failure_entry[3],
                    db=db,
                    upload_config_dir=tmp_path / "uploaded",
                    data_root=tmp_path,
                    llm_client_factory=lambda: object(),
                )
        finally:
            failure_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        success_future = executor.submit(run_success)
        failure_future = executor.submit(run_failure)
        success_result = success_future.result(timeout=10)
        failure_future.result(timeout=10)

    assert success_result["outcome"] == "complete"
    success_session = db.get_grading_session(success_entry[0])
    failed_session = db.get_grading_session(failure_entry[0])
    assert success_session is not None
    assert failed_session is not None
    assert success_session["source_paper_path"]
    assert (tmp_path / str(success_session["source_paper_path"])).is_file()
    assert (
        failed_session["rubric_path"],
        failed_session["answer_key_path"],
    ) == failure_entry[1]
    assert not failed_session["source_paper_path"]
    assert len({path for _thread_id, path, _reused in archive_results}) == 1


def test_source_generation_finalization_failure_preserves_committed_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="per_question",
    )
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )
    raw_papers = tmp_path / "question_bank" / "raw_papers"
    raw_papers.mkdir(parents=True)
    sentinel = raw_papers / "not-created-by-job.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with sqlite3.connect(db.db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_source_config_finish
            BEFORE UPDATE OF status ON jobs
            WHEN NEW.id = ? AND NEW.status = 'succeeded'
            BEGIN
                SELECT RAISE(ABORT, 'injected source finish failure');
            END
            """.replace("?", str(context.job_id))
        )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=lambda: object(),
    )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert result["outcome"] == "complete"
    assert (session["rubric_path"], session["answer_key_path"]) != old_paths
    assert Path(session["rubric_path"]).is_file()
    assert Path(session["answer_key_path"]).is_file()
    assert session["source_paper_path"]
    assert session["source_paper_sha256"] == source.sha256
    assert sentinel.read_text(encoding="utf-8") == "keep"
    stored = context.store.get_job(context.job_id)
    assert stored is not None
    assert stored.status == "running"
    assert stored.stage == "config_mapping"
    assert stored.result["mapping_status"] == "reconfirm_required"

    with sqlite3.connect(db.db_path) as connection:
        connection.execute("DROP TRIGGER fail_source_config_finish")
    assert context.store.fail_interrupted_jobs() == 1
    recovered = context.store.get_job(context.job_id)
    assert recovered is not None
    assert recovered.status == "succeeded"
    assert recovered.result["mapping_status"] == "reconfirm_required"


@pytest.mark.parametrize("same_sha", [False, True])
def test_atomic_source_binding_resets_sync_when_config_revision_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_sha: bool,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    db.bind_grading_session_source(
        session_id,
        source_paper_path="question_bank/raw_papers/old.docx",
        source_paper_sha256=source.sha256 if same_sha else "f" * 64,
    )
    db.update_question_bank_sync_state(
        session_id,
        state="ready",
        details={"confirmed": 1},
        error="old-error",
    )
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="per_question",
    )
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: _valid_config_payload(),
    )

    run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=lambda: object(),
    )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert session["source_paper_sha256"] == source.sha256
    assert session["question_bank_sync_state"] == "not_started"
    assert json.loads(session["question_bank_sync_details_json"]) == {}
    assert session["question_bank_sync_error"] is None
    assert session["question_bank_sync_updated_at"] is None


def test_source_generation_cancellation_preserves_old_binding_and_new_file_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="per_question",
    )
    context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )

    def cancel(*_args: object, **kwargs: object):
        assert store.request_cancel(context.job_id)
        kwargs["report"](0.8, "generation", "returned")
        return _valid_config_payload()

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        cancel,
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
    assert not list((tmp_path / "question_bank" / "raw_papers").glob("*"))


def test_targeted_regeneration_job_uses_current_payload_and_selected_question_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    loaded = load_editor_config(db, session_id)
    source_service, source = _controlled_source(tmp_path, session_id)
    input_id = stage_config_source_generation_input(
        tmp_path / "uploaded",
        session_id=session_id,
        expected_rubric_path=old_paths[0],
        expected_answer_key_path=old_paths[1],
        generation_mode="batched",
        source_id=source.source_id,
        source_revision=source.source_revision,
        decisions=[{
            "question_id": source.questions[0].question_id,
            "question_type": "proof",
            "excluded": False,
        }],
        existing_payload=loaded.payload,
        regenerate_question_ids=["Q1"],
        expected_revision=loaded.revision,
    )
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "regenerate_questions",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )
    captured: dict[str, object] = {}

    def targeted(existing_payload, confirmed_blocks, _document_text, **kwargs):
        captured["existing_payload"] = existing_payload
        captured["question_ids"] = kwargs["regenerate_question_ids"]
        captured["confirmed_ids"] = [
            block["question_id"] for block in confirmed_blocks
        ]
        return _valid_config_payload()

    monkeypatch.setattr(
        "backend.jobs.config_generation.regenerate_grading_config_questions",
        targeted,
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: pytest.fail("full generation must not run"),
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=lambda: object(),
    )

    assert result["outcome"] == "complete"
    assert captured["existing_payload"] == loaded.payload
    assert captured["question_ids"] == ["Q1"]
    assert captured["confirmed_ids"] == ["Q1"]


def test_config_generation_job_saves_partial_draft_without_binding_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(
        tmp_path,
        session_id,
        old_paths,
        question_ids=[f"Q{index}" for index in range(1, 7)],
    )
    context, _store = _job_context(
        db.db_path,
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


def test_config_generation_job_saves_score_pending_draft_without_binding_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(
        tmp_path,
        session_id,
        old_paths,
        question_ids=[f"Q{index}" for index in range(1, 7)],
    )
    context, _store = _job_context(
        db.db_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    pending = _valid_config_payload()
    pending["meta"] = {
        "warnings": [],
        "batches": [
            {
                "batch_id": "B001",
                "question_ids": ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"],
                "status": "succeeded",
            }
        ],
        "failed_batches": [],
        "failed_question_ids": [],
        "score_allocation_mode": "dedicated_ai_scoring",
        "score_allocation_ai_success": False,
        "score_allocation_pending": True,
        "score_allocation_failed": True,
        "score_allocation_error": "AI 统一配分失败（HTTP 502），未自动重试。",
    }
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: pending,
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
    assert result["generated_questions"] == 6
    assert result["failed_count"] == 0
    assert result["score_allocation_pending"] is True
    assert result["score_allocation_failed"] is True
    assert result["retryable"] is True
    draft = tmp_path / "uploaded" / f"config_generation_draft_job_{context.job_id}.json"
    assert json.loads(draft.read_text(encoding="utf-8"))["meta"]["score_allocation_pending"] is True


def test_partial_generation_rejects_a_source_replaced_while_model_was_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(tmp_path, session_id)
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="per_question",
    )
    context, _store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
        },
    )
    partial = _valid_config_payload()
    partial["meta"] = {
        "warnings": ["Q2 generation failed"],
        "failed_question_ids": ["Q2"],
        "failed_questions": [{"question_id": "Q2", "error": "temporary"}],
        "score_allocation_pending": True,
    }

    def replace_then_return(*_args: object, **_kwargs: object) -> dict[str, object]:
        asyncio.run(
            source_service.stage_and_parse(
                session_id=session_id,
                filename="replacement.docx",
                chunks=_chunks(_docx_bytes("1. Prove y equals y.\nAnswer: proven")),
            )
        )
        return partial

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        replace_then_return,
    )

    with pytest.raises(ConfigSourceChangedError):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )

    assert not (tmp_path / "uploaded" / f"config_generation_draft_job_{context.job_id}.json").exists()


def test_config_generation_job_honours_cancellation_before_final_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    context, store = _job_context(
        db.db_path,
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
    from backend.repositories.access import GradingRepositoryAccess

    assert isinstance(captured["db"], GradingRepositoryAccess)
    assert captured["upload_config_dir"] == tmp_path / "uploaded"
    assert captured["llm_client_factory"]() == "fake-client"


def test_config_generation_retry_loads_source_draft_and_selected_questions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    upload_dir = tmp_path / "uploaded"
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    store = JobStore(db.db_path)
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


def test_cancelled_retry_preserves_the_partial_jobs_shared_input(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    upload_dir = tmp_path / "uploaded"
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    store = JobStore(db.db_path)
    source = store.create_job(
        "config_generation",
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    store.finish(
        source.id,
        "succeeded",
        result={"session_id": session_id, "outcome": "partial", "retryable": True},
    )
    draft = upload_dir / f"config_generation_draft_job_{source.id}.json"
    draft.write_text(json.dumps(_valid_config_payload()), encoding="utf-8")
    retry = store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "retry",
            "source_job_id": source.id,
            "input_id": input_id,
        },
    )
    assert store.mark_running(retry.id)
    assert store.request_cancel(retry.id)
    context = JobContext(
        job_id=retry.id,
        job_type=retry.job_type,
        payload=retry.payload,
        store=store,
    )

    with pytest.raises(JobCancellationRequested):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=upload_dir,
            llm_client_factory=lambda: object(),
        )

    assert (upload_dir / f"config_generation_input_{input_id}.json").is_file()
    assert draft.is_file()


def test_config_generation_job_rejects_input_staged_for_another_session(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id + 1, old_paths)
    context, _store = _job_context(
        db.db_path,
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


def test_config_generation_job_does_not_overwrite_manual_config_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    context, _store = _job_context(
        db.db_path,
        {"session_id": session_id, "mode": "generate", "input_id": input_id},
    )
    manual_rubric = tmp_path / "manual-rubric.json"
    manual_answer = tmp_path / "manual-answer.json"
    manual_rubric.write_text("{}", encoding="utf-8")
    manual_answer.write_text("{}", encoding="utf-8")

    def change_config_then_return(*_args: object, **_kwargs: object) -> dict[str, object]:
        db.update_grading_session_config(
            session_id,
            rubric_path=str(manual_rubric),
            answer_key_path=str(manual_answer),
        )
        return _valid_config_payload()

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        change_config_then_return,
    )

    with pytest.raises(ValueError, match="config changed while generation was running"):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert session["rubric_path"] == str(manual_rubric)
    assert session["answer_key_path"] == str(manual_answer)
    assert not list((tmp_path / "uploaded").glob("rubric_job-*.json"))
    assert not list((tmp_path / "uploaded").glob("answer_key_job-*.json"))


def test_atomic_bind_rolls_back_session_when_committed_marker_fails(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    store = JobStore(db.db_path)
    job = store.create_job("config_generation", {"session_id": session_id})
    assert store.mark_running(job.id)
    with sqlite3.connect(db.db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_config_job_bind
            BEFORE UPDATE OF stage ON jobs
            WHEN NEW.id = ? AND NEW.stage = 'config_mapping'
            BEGIN
                SELECT RAISE(ABORT, 'injected finish failure');
            END
            """.replace("?", str(job.id))
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected finish failure"):
        store.finish_config_generation_and_bind(
            job.id,
            session_id=session_id,
            expected_rubric_path=old_paths[0],
            expected_answer_key_path=old_paths[1],
            rubric_path="new-rubric.json",
            answer_key_path="new-answer.json",
            result={"outcome": "complete"},
        )

    session = db.get_grading_session(session_id)
    assert session is not None
    assert (session["rubric_path"], session["answer_key_path"]) == old_paths
    assert store.get_job(job.id).status == "running"


def test_bound_config_job_stays_running_until_final_mapping_result_is_persisted(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    store = JobStore(db.db_path)
    job = store.create_job("config_generation", {"session_id": session_id})
    assert store.mark_running(job.id)
    provisional = {
        "session_id": session_id,
        "outcome": "complete",
        "mapping_status": "reconfirm_required",
        "mapping_message": "safe fallback",
    }

    assert store.finish_config_generation_and_bind(
        job.id,
        session_id=session_id,
        expected_rubric_path=old_paths[0],
        expected_answer_key_path=old_paths[1],
        rubric_path="new-rubric.json",
        answer_key_path="new-answer.json",
        result=provisional,
    )
    bound = store.get_job(job.id)
    assert bound is not None
    assert bound.status == "running"
    assert bound.stage == "config_mapping"
    assert bound.result["mapping_status"] == "reconfirm_required"
    with pytest.raises(ConfigSessionBusyError):
        store.create_claimed_config_job({"session_id": session_id, "mode": "generate"})

    final = {**provisional, "mapping_status": "refreshed", "mapping_message": "refreshed"}
    assert store.finalize_bound_config_generation(job.id, final)
    completed = store.get_job(job.id)
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.result == final


def test_restart_recovers_bound_config_as_conservative_success(tmp_path: Path) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    store = JobStore(db.db_path)
    job = store.create_job("config_generation", {"session_id": session_id})
    assert store.mark_running(job.id)
    provisional = {
        "session_id": session_id,
        "outcome": "complete",
        "mapping_status": "reconfirm_required",
        "mapping_message": "safe fallback",
    }
    assert store.finish_config_generation_and_bind(
        job.id,
        session_id=session_id,
        expected_rubric_path=old_paths[0],
        expected_answer_key_path=old_paths[1],
        rubric_path="new-rubric.json",
        answer_key_path="new-answer.json",
        result=provisional,
    )

    assert store.fail_interrupted_jobs() == 1
    recovered = store.get_job(job.id)
    assert recovered is not None
    assert recovered.status == "succeeded"
    assert recovered.result == provisional


def test_config_generation_restart_fails_interrupted_and_preserves_terminal_result(
    tmp_path: Path,
) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    store = JobStore(db.db_path)
    queued = store.create_job("config_generation", {"session_id": 1})
    running = store.create_job("config_generation", {"session_id": 1})
    succeeded = store.create_job("config_generation", {"session_id": 1})
    assert store.mark_running(running.id)
    store.finish(succeeded.id, "succeeded", result={"outcome": "partial"})

    manager = JobManager(JobStore(db.db_path), max_workers=1, cleanup_interrupted=True)
    try:
        assert manager.get(queued.id).status == "failed"
        assert manager.get(running.id).status == "failed"
        terminal = manager.get(succeeded.id)
        assert terminal.status == "succeeded"
        assert terminal.result == {"outcome": "partial"}
    finally:
        manager.shutdown()


def test_config_retry_claim_is_atomic_across_concurrent_submitters(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    source = store.create_job("config_generation", {"session_id": 1})
    store.finish(source.id, "succeeded", result={"outcome": "partial"})
    payload = {
        "session_id": 1,
        "mode": "retry",
        "source_job_id": source.id,
        "input_id": "a" * 32,
    }

    def submit() -> str:
        try:
            return f"job:{store.create_config_retry_job(payload).id}"
        except ConfigRetryAlreadySubmittedError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: submit(), range(2)))

    assert outcomes[0] == "conflict"
    assert outcomes[1].startswith("job:")


def test_config_session_claim_allows_exact_replay_but_rejects_another_active_request(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    first_payload = {
        "session_id": 7,
        "mode": "generate",
        "client_request_token": "1" * 32,
        "client_request_fingerprint": "a" * 64,
    }
    first, created = store.create_idempotent_config_job(first_payload)
    replay, replay_created = store.create_idempotent_config_job(first_payload)

    assert created is True
    assert replay_created is False
    assert replay.id == first.id
    with pytest.raises(ConfigSessionBusyError):
        store.create_idempotent_config_job(
            {
                **first_payload,
                "client_request_token": "2" * 32,
                "client_request_fingerprint": "b" * 64,
            }
        )
    with pytest.raises(ConfigSessionBusyError):
        store.create_claimed_config_job({"session_id": 7, "mode": "generate"})


def test_job_manager_marks_cancelled_error_terminal_instead_of_leaving_running(
    tmp_path: Path,
) -> None:
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def cancelled(_context: JobContext) -> dict[str, object]:
        raise asyncio.CancelledError()

    manager.register("config_generation", cancelled)
    try:
        job = manager.submit("config_generation", {"session_id": 7})
        manager.wait(job.id, timeout=5)
        terminal = manager.get(job.id)
        assert terminal is not None
        assert terminal.status == "failed"
        assert terminal.finished_at is not None
    finally:
        manager.shutdown()


def test_restart_cleans_only_interrupted_owned_inputs_and_preserves_retry_shared_input(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    upload_dir = tmp_path / "uploaded"
    generate_input = _stage_job_input(tmp_path, session_id, old_paths)
    refine_input = _stage_job_input(tmp_path, session_id, old_paths)
    retry_input = _stage_job_input(tmp_path, session_id, old_paths)
    store = JobStore(db.db_path)
    store.create_job(
        "config_generation",
        {"session_id": session_id, "mode": "generate", "input_id": generate_input},
    )
    store.create_job(
        "config_generation",
        {"session_id": session_id, "mode": "refine", "input_id": refine_input},
    )
    store.create_job(
        "config_generation",
        {"session_id": session_id, "mode": "retry", "input_id": retry_input},
    )

    manager = JobManager(
        JobStore(db.db_path),
        max_workers=1,
        cleanup_interrupted=True,
        interrupted_input_root=upload_dir,
    )
    try:
        assert not (upload_dir / f"config_generation_input_{generate_input}.json").exists()
        assert not (upload_dir / f"config_generation_input_{refine_input}.json").exists()
        assert (upload_dir / f"config_generation_input_{retry_input}.json").is_file()
    finally:
        manager.shutdown()


def test_restart_preserves_checkpointed_batch_input_and_exposes_partial_result(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    upload_dir = tmp_path / "uploaded"
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    store = JobStore(db.db_path)
    job = store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
        },
    )
    assert store.mark_running(job.id)
    draft_payload = _valid_config_payload()
    draft_payload["meta"] = {
        "generation_mode": "batched",
        "batch_size": 3,
        "batches": [
            {"batch_id": "B001", "question_ids": ["Q1"], "status": "succeeded"},
            {"batch_id": "B002", "question_ids": ["Q2"], "status": "pending"},
        ],
        "failed_batches": [
            {
                "batch_id": "B002",
                "question_ids": ["Q2"],
                "category": "pending",
                "error": "批次尚未开始",
            }
        ],
        "failed_question_ids": ["Q2"],
    }
    (upload_dir / f"config_generation_draft_job_{job.id}.json").write_text(
        json.dumps(draft_payload, ensure_ascii=False), encoding="utf-8"
    )

    manager = JobManager(
        JobStore(db.db_path),
        max_workers=1,
        cleanup_interrupted=True,
        interrupted_input_root=upload_dir,
    )
    try:
        recovered = manager.get(job.id)
        assert recovered is not None
        assert recovered.status == "failed"
        assert recovered.result["outcome"] == "partial"
        assert recovered.result["failed_question_ids"] == ["Q2"]
        assert recovered.result["failed_batches"][0]["batch_id"] == "B002"
        assert (upload_dir / f"config_generation_input_{input_id}.json").is_file()
    finally:
        manager.shutdown()


def test_config_source_references_are_derived_from_private_job_payloads(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    complete = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "source_id": "9" * 32,
            "source_revision": "8" * 64,
        },
    )
    store.finish(complete.id, "succeeded", result={"outcome": "complete"})
    partial = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "source_id": "7" * 32,
            "source_revision": "6" * 64,
        },
    )
    store.finish(partial.id, "succeeded", result={"outcome": "partial"})
    store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "generate",
            "generation_mode": "per_question",
            "source_id": "a" * 32,
            "source_revision": "b" * 64,
            "input_id": "c" * 32,
        },
    )
    store.create_job(
        "config_generation",
        {
            "session_id": 8,
            "source_id": "d" * 32,
            "source_revision": "e" * 64,
        },
    )
    store.create_job(
        "report_export",
        {"session_id": 7, "source_id": "f" * 32},
    )

    assert store.referenced_config_source_ids(7) == {"a" * 32, "7" * 32}


def test_completed_retry_chain_releases_only_consumed_partial_source_references(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    source_id = "7" * 32
    input_id = "8" * 32
    first = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "generate",
            "generation_mode": "per_question",
            "source_id": source_id,
            "source_revision": "6" * 64,
            "input_id": input_id,
        },
    )
    store.finish(first.id, "succeeded", result={"outcome": "partial"})
    second = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "retry",
            "generation_mode": "per_question",
            "source_job_id": first.id,
            "source_id": source_id,
            "source_revision": "6" * 64,
            "input_id": input_id,
        },
    )
    store.finish(second.id, "succeeded", result={"outcome": "partial"})

    assert store.referenced_config_source_ids(7) == {source_id}

    final = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "retry",
            "generation_mode": "per_question",
            "source_job_id": second.id,
            "source_id": source_id,
            "source_revision": "6" * 64,
            "input_id": input_id,
        },
    )
    store.finish(final.id, "succeeded", result={"outcome": "complete"})

    assert store.consumed_config_retry_artifacts(7) == (
        (7, first.id, input_id),
        (7, second.id, input_id),
    )
    assert store.referenced_config_source_ids(7) == set()

    active = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "generate",
            "source_id": source_id,
            "source_revision": "6" * 64,
            "input_id": "9" * 32,
        },
    )
    assert active.status == "queued"
    assert store.referenced_config_source_ids(7) == {source_id}


def test_completed_retry_cleanup_removes_only_consumed_artifacts(
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploaded"
    upload_dir.mkdir()
    store = JobStore(tmp_path / "jobs.db")
    input_id = "8" * 32
    source = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "generate",
            "generation_mode": "per_question",
            "input_id": input_id,
        },
    )
    store.finish(source.id, "succeeded", result={"outcome": "partial"})
    retry = store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "retry",
            "generation_mode": "per_question",
            "source_job_id": source.id,
            "input_id": input_id,
        },
    )
    store.finish(retry.id, "succeeded", result={"outcome": "complete"})
    draft = upload_dir / f"config_generation_draft_job_{source.id}.json"
    staged_input = upload_dir / f"config_generation_input_{input_id}.json"
    sentinel = upload_dir / "keep.json"
    draft.write_text("{}", encoding="utf-8")
    staged_input.write_text("{}", encoding="utf-8")
    sentinel.write_text("keep", encoding="utf-8")

    cleanup_consumed_config_retry_artifacts(upload_dir, store, session_id=7)

    assert not draft.exists()
    assert not staged_input.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def _refine_context(tmp_path: Path):
    db, session_id, old_paths = _db_with_session(tmp_path)
    db.bind_grading_session_source(
        session_id,
        source_paper_path="papers/original.docx",
        source_paper_sha256="e" * 64,
    )
    current = load_editor_config(db, session_id)
    candidate = apply_config_editor_changes(
        current.payload,
        edits=(),
        commands=(
            ReplaceScoringUnitsCommand(
                kind="replace_parts",
                question_id="Q1",
                parts=(
                    ManualPartInput(part_id="Q1(P1)", score=8, core_goal="first"),
                    ManualPartInput(part_id="Q1(P2)", score=9, core_goal="second"),
                ),
            ),
        ),
    )
    input_id = stage_config_refine_input(
        tmp_path / "uploaded",
        session_id=session_id,
        expected_rubric_path=old_paths[0],
        expected_answer_key_path=old_paths[1],
        expected_revision=current.revision,
        existing_payload=current.payload,
        commands=[
            {
                "kind": "replace_parts",
                "question_id": "Q1",
                "parts": [
                    {"part_id": "Q1(P1)", "score": 8, "core_goal": "first"},
                    {"part_id": "Q1(P2)", "score": 9, "core_goal": "second"},
                ],
            }
        ],
    )
    context, store = _job_context(
        db.db_path,
        {"session_id": session_id, "mode": "refine", "input_id": input_id},
    )
    return db, session_id, old_paths, candidate, context, store


def test_refine_job_preserves_teacher_part_ids_and_calls_factory_once(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths, candidate, context, _store = _refine_context(tmp_path)
    calls = 0

    class Client:
        def json_from_text(
            self,
            _prompt: str,
            *,
            model: str | None = None,
        ) -> dict[str, object]:
            assert model is None
            return json.loads(json.dumps(candidate))

    client = Client()

    def factory():
        nonlocal calls
        calls += 1
        return client

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        llm_client_factory=factory,
    )

    assert result["outcome"] == "complete"
    assert calls == 1
    current = db.get_grading_session(session_id)
    assert (current["rubric_path"], current["answer_key_path"]) != old_paths
    assert current["source_paper_path"] == "papers/original.docx"
    assert current["source_paper_sha256"] == "e" * 64
    published = load_editor_config(db, session_id)
    assert editor_part_ids(published.payload) == editor_part_ids(
        canonicalize_grading_config_payload(candidate)
    )


def test_refine_job_rejects_changed_teacher_part_ids_and_preserves_old_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths, candidate, context, _store = _refine_context(tmp_path)

    def changed(payload, **_kwargs):
        result = json.loads(json.dumps(payload))
        result["rubric"]["questions"][0]["parts"][0]["part_id"] = "changed-id"
        result["answer_key"]["questions"][0]["parts"][0]["part_id"] = "changed-id"
        return result

    monkeypatch.setattr(
        "backend.jobs.config_generation.refine_grading_config_from_manual_structure",
        changed,
    )
    with pytest.raises(ValueError, match="changed teacher scoring-unit identities"):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )
    current = db.get_grading_session(session_id)
    assert (current["rubric_path"], current["answer_key_path"]) == old_paths
    assert not list((tmp_path / "uploaded").glob("rubric_job-*.json"))


def test_refine_job_rejects_changed_teacher_step_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _session_id, _old_paths, _candidate, context, _store = _refine_context(tmp_path)

    def changed(payload, **_kwargs):
        result = json.loads(json.dumps(payload))
        steps = result["rubric"]["questions"][0]["parts"][0].setdefault("steps", [])
        if steps:
            steps[0]["step_id"] = "model-replaced-step"
        else:
            steps.append(
                {
                    "step_id": "model-added-step",
                    "step_score": 8,
                    "core_goal": "changed",
                    "required_elements": [],
                    "allow_alternative_methods": True,
                }
            )
        return result

    monkeypatch.setattr(
        "backend.jobs.config_generation.refine_grading_config_from_manual_structure",
        changed,
    )
    with pytest.raises(ValueError, match="changed teacher scoring-unit identities"):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )


def test_refine_cancel_after_model_is_cancelled_before_publish_and_deletes_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _session_id, _old_paths, _candidate, context, store = _refine_context(tmp_path)
    input_id = str(context.payload["input_id"])

    def cancel_then_return(payload, **_kwargs):
        assert store.request_cancel(context.job_id)
        return json.loads(json.dumps(payload))

    monkeypatch.setattr(
        "backend.jobs.config_generation.refine_grading_config_from_manual_structure",
        cancel_then_return,
    )
    with pytest.raises(JobCancellationRequested):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=lambda: object(),
        )
    assert not (tmp_path / "uploaded" / f"config_generation_input_{input_id}.json").exists()
    assert list((tmp_path / "uploaded").glob("rubric_job-*.json")) == []


def test_batch_cancellation_keeps_checkpoint_and_input_for_manual_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    upload_dir = tmp_path / "uploaded"
    input_id = _stage_job_input(tmp_path, session_id, old_paths)
    context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
        },
    )
    draft_payload = _valid_config_payload()
    draft_payload["meta"] = {
        "generation_mode": "batched",
        "batch_size": 3,
        "batches": [
            {"batch_id": "B001", "question_ids": ["Q1"], "status": "succeeded"},
            {"batch_id": "B002", "question_ids": ["Q2"], "status": "pending"},
        ],
        "failed_batches": [
            {
                "batch_id": "B002",
                "question_ids": ["Q2"],
                "category": "pending",
                "error": "批次尚未开始",
            }
        ],
        "failed_question_ids": ["Q2"],
    }

    def cancel_after_checkpoint(*_args: object, **kwargs: object) -> dict[str, object]:
        assert store.request_cancel(context.job_id)
        kwargs["checkpoint"](draft_payload)
        raise AssertionError("checkpoint must stop before another batch starts")

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        cancel_after_checkpoint,
    )

    with pytest.raises(JobCancellationRequested):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=upload_dir,
            llm_client_factory=lambda: object(),
        )

    cancelled = store.get_job(context.job_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.result["outcome"] == "partial"
    assert cancelled.result["failed_question_ids"] == ["Q2"]
    assert (upload_dir / f"config_generation_input_{input_id}.json").is_file()
    assert (upload_dir / f"config_generation_draft_job_{context.job_id}.json").is_file()


def test_refine_job_rejects_same_path_old_revision_before_model_call(tmp_path: Path) -> None:
    db, session_id, old_paths, _candidate, context, _store = _refine_context(tmp_path)
    rubric_path = Path(old_paths[0])
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["exam_title"] = "Concurrent edit"
    rubric_path.write_text(json.dumps(rubric), encoding="utf-8")
    called = False

    def factory():
        nonlocal called
        called = True
        return object()

    with pytest.raises(ValueError, match="changed before refinement started"):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            llm_client_factory=factory,
        )
    assert called is False


def _deferred_taxonomy_contract() -> dict[str, Any]:
    return {
        "taxonomy_revision": 2,
        "allowed_dimensions": ["knowledge", "ability", "curriculum"],
        "candidates": {
            "knowledge": [
                {
                    "id": "kp_alg_linear_equation",
                    "name": "一元一次方程",
                    "aliases": ["一次方程"],
                }
            ],
            "ability": [
                {
                    "id": "ability-calculation",
                    "name": "运算能力",
                    "aliases": [],
                }
            ],
        },
        "curriculum_volume": {
            "id": "bnu24-math-g7-upper",
            "sections": [
                {
                    "id": "synthetic-linear-equation-section",
                    "chapter_name": "一元一次方程",
                }
            ],
        },
    }


def test_config_analysis_keeps_multiple_images_for_one_question_role() -> None:
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode("ascii")

    images = _config_analysis_images(
        {"question": [encoded, encoded], "answer": encoded}
    )

    assert [image.role for image in images] == ["question", "question", "answer"]


def _deferred_combined_item(question_id: int) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "tag_analysis": {
            "knowledge_points": ["一元一次方程"],
            "method_tags": [],
            "ability_tags": ["运算能力"],
            "math_model_tags": [],
            "special_type_tags": [],
            "difficulty": 3,
            "error_prone_points": ["运算化简错误"],
            "prerequisite_points": [],
            "textbook_chapters": [],
            "curriculum_sections": ["synthetic-linear-equation-section"],
            "suitable_student_level": "",
            "canonical_knowledge_id": "kp_alg_linear_equation",
            "taxonomy_revision": 2,
            "proposed_tags": [],
            "reason": "合成分析。",
            "confidence": 0.95,
        },
        "solution_evidence": {
            "schema_version": "question-solution-evidence-v2",
            "question_id": question_id,
            "parts": [
                {
                    "part_id": f"part-{question_id}",
                    "label": f"第{question_id}题",
                    "response_mode": "process_required",
                    "canonical_answer": "x=1",
                    "accepted_forms": ["x=1"],
                    "full_answer": "移项并化简得 x=1。",
                    "proof_obligations": [],
                    "visual_requirements": [],
                    "deduction_policy": ["缺少关键变形时该步未达成"],
                    "allow_alternative_methods": True,
                    "evidence_points": [
                        {
                            "evidence_point_id": f"step-{question_id}",
                            "step_index": 1,
                            "target": "完成等价变形",
                            "justification": "依据等式性质移项并化简",
                            "answer_anchor": "移项并化简",
                            "observable_evidence": "写出正确的移项和化简过程",
                            "depends_on": [],
                            "fine_term_links": [
                                {
                                    "fine_term_id": "kp_alg_linear_equation",
                                    "fine_term_name": "一元一次方程",
                                    "role": "direct",
                                }
                            ],
                            "equivalent_rules": [],
                            "counterexamples": ["移项后未变号"],
                        },
                        {
                            "evidence_point_id": f"result-{question_id}",
                            "step_index": 2,
                            "target": "得出方程的解",
                            "justification": "由前一步的等价方程求解未知数",
                            "answer_anchor": "x=1",
                            "observable_evidence": "写出 x=1 并作为最终结论",
                            "depends_on": [f"step-{question_id}"],
                            "fine_term_links": [
                                {
                                    "fine_term_id": "kp_alg_linear_equation",
                                    "fine_term_name": "一元一次方程",
                                    "role": "direct",
                                }
                            ],
                            "equivalent_rules": ["1=x"],
                            "counterexamples": ["只写中间式未给出解"],
                        }
                    ],
                }
            ],
            "auxiliary_rules": [],
            "rationale": "按可观察步骤拆分。",
            "confidence": 0.95,
        },
    }


class _DeferredProtocolResponse:
    def __init__(self, question_ids: list[int]) -> None:
        self.output_text = json.dumps(
            {
                "results": [
                    _deferred_combined_item(question_id)
                    for question_id in question_ids
                ]
            },
            ensure_ascii=False,
        )
        self.usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }


class _DeferredProtocol:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def responses(self, **kwargs: Any) -> _DeferredProtocolResponse:
        self.calls.append(kwargs)
        prompt = kwargs["kwargs"]["input"][1]["content"][0]["text"]
        questions = json.loads(prompt)["questions"]
        return _DeferredProtocolResponse(
            [int(item["question_id"]) for item in questions]
        )


class _InterruptingDeferredProtocol(_DeferredProtocol):
    def responses(self, **kwargs: Any) -> _DeferredProtocolResponse:
        self.calls.append(kwargs)
        raise KeyboardInterrupt()


class _DeferredTaggingService:
    model = "synthetic-combined-v3"

    def __init__(self, protocol: _DeferredProtocol) -> None:
        self.protocol = protocol

    def _protocol_adapter(self) -> _DeferredProtocol:
        return self.protocol

    def taxonomy_contracts(
        self,
        contexts: dict[int, object],
    ) -> dict[int, dict[str, Any]]:
        return {
            question_id: _deferred_taxonomy_contract()
            for question_id in contexts
        }


class _DeferredScoreClient:
    def __init__(self, *, valid_six_question_score: bool) -> None:
        self.valid_six_question_score = valid_six_question_score
        self.calls: list[str] = []
        self.settings = SimpleNamespace(config_model="synthetic-score-only")

    def json_from_text_once(
        self,
        prompt: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(prompt)
        structure = json.loads(prompt.split("待分值结构：\n", 1)[1])
        if self.valid_six_question_score:
            assert len(structure) == 6
            scores = [17, 17, 17, 17, 17, 15]
        else:
            assert len(structure) == 1
            # Deliberately violates the existing 18-point cap.  The job must
            # checkpoint the finished analysis and leave only scoring pending.
            scores = [100]
        return {
            "question_scores": [
                {
                    "question_id": item["question_id"],
                    "max_score": score,
                    "parts": [
                        {
                            "part_id": part["part_id"],
                            "part_score": score,
                            "steps": [
                                {
                                    "step_id": step["step_id"],
                                    "step_score": (
                                        score // len(part["steps"])
                                        + (
                                            1
                                            if step_index
                                            < score % len(part["steps"])
                                            else 0
                                        )
                                    ),
                                }
                                for step_index, step in enumerate(part["steps"])
                            ],
                        }
                        for part in item["parts"]
                    ],
                }
                for item, score in zip(structure, scores, strict=True)
            ]
        }


def test_evidence_analysis_checkpoint_is_reused_by_score_retry_without_model_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    _source_service, source = _controlled_source(
        tmp_path,
        session_id,
        text="1. 解方程 x+1=2。\n答案：x=1",
    )
    input_id = _stage_controlled_input(
        tmp_path,
        _source_service,
        source,
        old_paths,
        generation_mode="batched",
        sync_to_question_bank=True,
    )
    first_context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    protocol = _DeferredProtocol()
    tagging_service = _DeferredTaggingService(protocol)
    tagging_factory_calls = 0

    def tagging_factory() -> _DeferredTaggingService:
        nonlocal tagging_factory_calls
        tagging_factory_calls += 1
        return tagging_service

    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: pytest.fail(
            "the legacy structure generator must not run"
        ),
    )
    score_client = _DeferredScoreClient(valid_six_question_score=False)

    first = run_config_generation_job(
        context=first_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: score_client,
        tagging_ai_service_factory=tagging_factory,
        taxonomy_governance=object(),
    )

    assert first["outcome"] == "partial"
    assert first["score_allocation_pending"] is True
    assert len(protocol.calls) == 1
    assert len(score_client.calls) == 1
    assert "SCORE_QUESTION_IDS_JSON" in score_client.calls[0]
    assert "BATCH_QUESTION_IDS_JSON" not in score_client.calls[0]
    assert tagging_factory_calls == 1
    store.finish(first_context.job_id, "succeeded", result=first)
    completed_first = store.get_job(first_context.job_id)
    assert completed_first is not None
    assert completed_first.status == "succeeded"
    artifact_files = list(
        (tmp_path / "uploaded").glob("deferred_question_analysis_*.json")
    )
    assert len(artifact_files) == 1

    retry_context, _retry_store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "retry",
            "generation_mode": "batched",
            "source_job_id": first_context.job_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    retried = run_config_generation_job(
        context=retry_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: score_client,
        tagging_ai_service_factory=tagging_factory,
        taxonomy_governance=object(),
    )

    assert retried["outcome"] == "partial"
    assert len(protocol.calls) == 1
    assert tagging_factory_calls == 1
    assert len(score_client.calls) == 2


def test_local_quality_failed_question_retry_reanalyzes_question_before_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(
        tmp_path,
        session_id,
        text="1. 说明解方程 x+1=2 的每一步。\n答案：x=1",
    )
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="batched",
        sync_to_question_bank=True,
    )
    first_context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    protocol = _DeferredProtocol()
    allocation_calls: list[dict[str, Any]] = []

    def allocate_with_first_result_too_shallow(
        structure: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        payload = copy.deepcopy(structure)
        allocation_calls.append(payload)
        question = payload["rubric"]["questions"][0]
        question["question_type"] = "comprehensive"
        question["max_score"] = 100
        part = question["parts"][0]
        part["part_score"] = 100
        steps = part["steps"]
        if len(allocation_calls) == 1:
            del steps[1:]
            steps[0]["core_goal"] = "合理的推理过程"
        for index, step in enumerate(steps):
            step["step_score"] = 100 / len(steps)
            step["step_id"] = f"S{index + 1}"
        payload["rubric"]["total_score"] = 100
        scoring_pending = len(allocation_calls) > 1
        payload["meta"].update(
            {
                "score_allocation_mode": "dedicated_ai_scoring",
                "score_allocation_ai_success": not scoring_pending,
                "score_allocation_pending": scoring_pending,
                "score_allocation_failed": scoring_pending,
            }
        )
        return payload

    monkeypatch.setattr(
        "backend.jobs.config_generation.allocate_grading_config_scores",
        allocate_with_first_result_too_shallow,
    )
    first = run_config_generation_job(
        context=first_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: object(),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
    )

    assert first["failed_question_ids"] == ["Q1"]
    assert first["failed_batches"][0]["category"] == "local_validation"
    assert len(protocol.calls) == 1
    assert len(allocation_calls) == 1
    store.finish(first_context.job_id, "succeeded", result=first)

    artifact_path = next(
        (tmp_path / "uploaded").glob("deferred_question_analysis_*.json")
    )
    artifact_bytes = artifact_path.read_bytes()
    artifact_path.unlink()
    missing_context, _missing_store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "retry",
            "generation_mode": "batched",
            "source_job_id": first_context.job_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
            "retry_question_ids": ["Q1"],
        },
    )
    with pytest.raises(ValueError, match="分析断点已丢失"):
        _run_config_generation_job_impl(
            context=missing_context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            data_root=tmp_path / "data",
            llm_client_factory=lambda: object(),
            tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
            taxonomy_governance=object(),
        )
    assert len(protocol.calls) == 1
    assert len(allocation_calls) == 1
    artifact_path.write_bytes(artifact_bytes)

    retry_context, _retry_store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "retry",
            "generation_mode": "batched",
            "source_job_id": first_context.job_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
            "retry_question_ids": ["Q1"],
        },
    )
    retried = run_config_generation_job(
        context=retry_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: object(),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
    )

    assert len(protocol.calls) == 2
    assert len(allocation_calls) == 2
    assert retried["failed_question_ids"] == []


def test_evidence_granularity_retry_reanalyzes_before_first_score_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(
        tmp_path,
        session_id,
        text=(
            "1. 在直角三角形中，AD=AC，按步骤求出y与x的关系。\n"
            "答案：先求∠B，再求∠ACD，最后得到y=x/2。"
        ),
    )
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="batched",
        sync_to_question_bank=True,
    )

    class GranularityRetryProtocol(_DeferredProtocol):
        def responses(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            prompt = kwargs["kwargs"]["input"][1]["content"][0]["text"]
            question_id = int(json.loads(prompt)["questions"][0]["question_id"])
            item = _deferred_combined_item(question_id)
            if len(self.calls) == 1:
                part = item["solution_evidence"]["parts"][0]
                part["full_answer"] = (
                    "先得到∠B=90°-x；再得到∠ACD=90°-x/2；最后推出y=x/2。"
                )
                part["evidence_points"] = [
                    {
                        "evidence_point_id": f"step-{question_id}",
                        "step_index": 1,
                        "target": "完成全部角度推导并推出y=x/2",
                        "justification": "综合使用内角和与等腰三角形性质",
                        "answer_anchor": "y=x/2",
                        "observable_evidence": "写出完整推导",
                        "depends_on": [],
                        "fine_term_links": [],
                        "equivalent_rules": [],
                        "counterexamples": [],
                    }
                ]
            return SimpleNamespace(
                output_text=json.dumps({"results": [item]}, ensure_ascii=False),
                usage={
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                },
            )

    protocol = GranularityRetryProtocol()
    allocation_calls: list[dict[str, Any]] = []

    def allocate_after_valid_analysis(
        structure: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        payload = copy.deepcopy(structure)
        allocation_calls.append(payload)
        question = payload["rubric"]["questions"][0]
        question["question_type"] = "comprehensive"
        question["max_score"] = 100
        part = question["parts"][0]
        part["part_score"] = 100
        steps = part["steps"]
        for index, step in enumerate(steps):
            step["step_score"] = 50
            step["step_id"] = str(step["step_id"])
        payload["rubric"]["total_score"] = 100
        payload["meta"].update(
            {
                "score_allocation_mode": "dedicated_ai_scoring",
                "score_allocation_ai_success": False,
                "score_allocation_pending": True,
                "score_allocation_failed": True,
            }
        )
        return payload

    monkeypatch.setattr(
        "backend.jobs.config_generation.allocate_grading_config_scores",
        allocate_after_valid_analysis,
    )
    first_context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    first = run_config_generation_job(
        context=first_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: object(),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
    )

    assert first["failed_question_ids"] == ["Q1"]
    assert first["failed_batches"][0]["category"] == (
        "evidence_granularity_insufficient"
    )
    assert len(protocol.calls) == 1
    assert allocation_calls == []
    store.finish(first_context.job_id, "succeeded", result=first)

    retry_context, _retry_store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "retry",
            "generation_mode": "batched",
            "source_job_id": first_context.job_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
            "retry_question_ids": ["Q1"],
        },
    )
    retried = run_config_generation_job(
        context=retry_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: object(),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
    )

    assert len(protocol.calls) == 2
    assert len(allocation_calls) == 1
    assert retried["failed_question_ids"] == []


def test_interrupted_evidence_request_is_reported_uncertain_without_model_replay(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    source_service, source = _controlled_source(
        tmp_path,
        session_id,
        text="1. 解方程 x+1=2。\n答案：x=1",
    )
    input_id = _stage_controlled_input(
        tmp_path,
        source_service,
        source,
        old_paths,
        generation_mode="batched",
        sync_to_question_bank=True,
    )
    first_context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    interrupted_protocol = _InterruptingDeferredProtocol()

    with pytest.raises(KeyboardInterrupt):
        _run_config_generation_job_impl(
            context=first_context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            data_root=tmp_path / "data",
            llm_client_factory=lambda: pytest.fail("scoring must not start"),
            tagging_ai_service_factory=lambda: _DeferredTaggingService(
                interrupted_protocol
            ),
            taxonomy_governance=object(),
        )

    # Calling the implementation directly models a hard process exit: the normal
    # wrapper cannot run cleanup, so both the durable artifact and input survive.
    staged = load_config_generation_input(tmp_path / "uploaded", input_id)
    assert staged["analysis_artifact_id"]
    assert len(interrupted_protocol.calls) == 1
    assert preserve_interrupted_config_generation_checkpoints(
        tmp_path / "uploaded",
        store,
    ) == {input_id}
    interrupted_job = store.get_job(first_context.job_id)
    assert interrupted_job is not None
    assert interrupted_job.result["outcome"] == "partial"
    assert interrupted_job.result["uncertain_question_ids"] == ["Q1"]
    assert interrupted_job.result["needs_teacher_resolution"] is True
    assert interrupted_job.result["retryable"] is False

    replay_protocol = _DeferredProtocol()
    resume_context, resume_store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    resumed = run_config_generation_job(
        context=resume_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: pytest.fail("scoring must not start"),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(replay_protocol),
        taxonomy_governance=object(),
    )
    assert resumed["outcome"] == "partial"
    assert resumed["uncertain_question_ids"] == ["Q1"]
    assert resumed["retryable"] is False
    assert replay_protocol.calls == []

    resume_store.finish(resume_context.job_id, "succeeded", result=resumed)
    confirmed_protocol = _DeferredProtocol()
    score_client = _DeferredScoreClient(valid_six_question_score=False)
    confirmed_context, _confirmed_store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "retry",
            "generation_mode": "batched",
            "source_job_id": resume_context.job_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
            "retry_question_ids": ["Q1"],
            "confirm_uncertain_retry": True,
        },
    )
    confirmed = run_config_generation_job(
        context=confirmed_context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: score_client,
        tagging_ai_service_factory=lambda: _DeferredTaggingService(
            confirmed_protocol
        ),
        taxonomy_governance=object(),
    )

    assert len(confirmed_protocol.calls) == 1
    assert confirmed["generated_questions"] == 1
    assert confirmed.get("uncertain_question_ids") is None
    assert confirmed["score_allocation_pending"] is True
    assert len(score_client.calls) == 1


def test_complete_evidence_first_generation_queues_exact_artifact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, old_paths = _db_with_session(tmp_path)
    lines = [f"{index}. 解方程 x+{index}={index + 1}。" for index in range(1, 7)]
    lines.extend(
        ["答案", *[f"{index}. x=1" for index in range(1, 7)]]
    )
    _source_service, source = _controlled_source(
        tmp_path,
        session_id,
        text="\n".join(lines),
    )
    assert len(source.questions) == 6
    input_id = stage_config_source_generation_input(
        tmp_path / "uploaded",
        session_id=session_id,
        expected_rubric_path=old_paths[0],
        expected_answer_key_path=old_paths[1],
        generation_mode="batched",
        source_id=source.source_id,
        source_revision=source.source_revision,
        sync_to_question_bank=True,
        curriculum_volume_id="bnu24-math-g7-upper",
        decisions=[
            {
                "question_id": question.question_id,
                "question_type": "calculation",
                "excluded": False,
            }
            for question in source.questions
        ],
    )
    base_context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )
    submitted: list[dict[str, object]] = []
    context = JobContext(
        job_id=base_context.job_id,
        job_type=base_context.job_type,
        payload=base_context.payload,
        store=store,
        question_bank_sync_submitter=lambda payload: (
            submitted.append(payload)
            or SimpleNamespace(id=193, status="queued"),
            True,
        ),
    )
    progress_reports: list[tuple[float, str, str]] = []
    update_progress = store.update_progress

    def record_progress(
        job_id: int,
        *,
        progress: float,
        stage: str,
        detail: str = "",
    ) -> None:
        progress_reports.append((progress, stage, detail))
        update_progress(
            job_id,
            progress=progress,
            stage=stage,
            detail=detail,
        )

    monkeypatch.setattr(store, "update_progress", record_progress)
    protocol = _DeferredProtocol()
    score_client = _DeferredScoreClient(valid_six_question_score=True)
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: pytest.fail(
            "the legacy structure generator must not run"
        ),
    )

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        llm_client_factory=lambda: score_client,
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
    )

    assert result["outcome"] == "complete", json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert len(score_client.calls) == 1
    analysis_reports = [
        item
        for item in progress_reports
        if item[1] == "question_analysis" and item[0] > 0.08
    ]
    assert [item[0] for item in analysis_reports] == sorted(
        item[0] for item in analysis_reports
    )
    assert len(analysis_reports) >= 2
    assert analysis_reports[-1][0] == pytest.approx(0.84)
    assert "6/6" in analysis_reports[-1][2]
    assert len(submitted) == 1
    queued = submitted[0]
    assert queued["analysis_source_id"] == source.source_id
    assert queued["analysis_source_revision"] == source.source_revision
    artifact = DeferredAnalysisArtifactStore(tmp_path / "uploaded").load(
        str(queued["analysis_artifact_id"]),
        session_id=session_id,
        source_id=source.source_id,
        source_revision=source.source_revision,
        curriculum_volume_id="bnu24-math-g7-upper",
        expected_content_hash=str(queued["analysis_artifact_hash"]),
    )
    assert artifact.bundle.status == "succeeded"
    assert [item.source_question_ref for item in artifact.bundle.items] == [
        f"Q{index}" for index in range(1, 7)
    ]
