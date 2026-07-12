from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.jobs.question_import import run_question_import_job
from backend.jobs.store import JobStore
from question_bank.database.schema import connect, initialize_database
from question_bank.importers.batch_importer import (
    BatchImportResult,
    PaperImportFileResult,
)
from question_bank.services.question_write_service import (
    QuestionBankWriteService,
    QuestionImportUploadNotFound,
)


def _service_and_request(tmp_path: Path):
    service = QuestionBankWriteService(
        tmp_path / "data" / "databases" / "question_bank.db",
        data_root=tmp_path / "data",
    )
    upload = service.stage_upload(filename="测试试卷.docx", content=b"fake-docx")
    return service, service.create_import_request(upload_id=upload.upload_id)


def _context(tmp_path: Path, payload: dict[str, object]) -> tuple[JobContext, JobStore]:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("question_import", payload)
    assert store.mark_running(job.id)
    return JobContext(job.id, job.job_type, job.payload, store), store


def _successful_importer(scanned, database_path, **_kwargs):
    initialize_database(database_path)
    source = str(scanned[0].source_file)
    with connect(database_path) as conn:
        paper_id = int(
            conn.execute(
                "INSERT INTO papers (title, source_file, import_status) VALUES (?, ?, ?)",
                ("测试试卷", source, "imported"),
            ).lastrowid
        )
        for number in ("1", "2"):
            conn.execute(
                """
                INSERT INTO questions (paper_id, question_number, question_text, source_file)
                VALUES (?, ?, ?, ?)
                """,
                (paper_id, number, f"第{number}题测试题干", source),
            )
    return BatchImportResult(
        files=[PaperImportFileResult(source, "imported", question_count=2)],
        imported_papers=1,
        question_count=2,
        answer_match_count=0,
        review_count=0,
        skipped_duplicate_files=0,
        failed_files=0,
    )


def test_load_import_resource_rejects_missing_and_tampered_request(tmp_path: Path) -> None:
    service, request = _service_and_request(tmp_path)

    resource = service.load_import_resource(request.request_id)
    assert resource.source_path.read_bytes() == b"fake-docx"
    assert resource.request_id == request.request_id

    with pytest.raises(QuestionImportUploadNotFound):
        service.load_import_resource("../outside")

    resource.source_path.write_bytes(b"tampered")
    with pytest.raises(QuestionImportUploadNotFound):
        service.load_import_resource(request.request_id)


def test_question_import_job_uses_server_request_and_returns_safe_ids(
    tmp_path: Path,
) -> None:
    service, request = _service_and_request(tmp_path)
    context, _store = _context(tmp_path, {"request_id": request.request_id})

    result = run_question_import_job(
        context=context,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=_successful_importer,
    )

    assert result == {
        "request_id": request.request_id,
        "outcome": "complete",
        "imported_papers": 1,
        "question_count": 2,
        "failed_count": 0,
        "successful_question_ids": [1, 2],
        "failed_question_ids": [],
        "failure_category": "",
        "retryable": False,
    }
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)


def test_question_import_job_maps_import_failure_to_safe_retryable_summary(
    tmp_path: Path,
) -> None:
    service, request = _service_and_request(tmp_path)
    context, _store = _context(tmp_path, {"request_id": request.request_id})

    def failed_importer(scanned, _database_path, **_kwargs):
        source = str(scanned[0].source_file)
        return BatchImportResult(
            files=[
                PaperImportFileResult(
                    source,
                    "failed",
                    message=f"API key=secret failed at {tmp_path}",
                )
            ],
            imported_papers=0,
            question_count=0,
            answer_match_count=0,
            review_count=0,
            skipped_duplicate_files=0,
            failed_files=1,
        )

    result = run_question_import_job(
        context=context,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=failed_importer,
    )

    assert result["outcome"] == "failed"
    assert result["failed_count"] == 1
    assert result["failure_category"] == "import"
    assert result["retryable"] is True
    assert "secret" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_question_import_job_masks_unexpected_importer_exception(tmp_path: Path) -> None:
    service, request = _service_and_request(tmp_path)
    context, _store = _context(tmp_path, {"request_id": request.request_id})

    def exploding_importer(*_args, **_kwargs):
        raise RuntimeError(f"API key=super-secret at {tmp_path}")

    with pytest.raises(RuntimeError, match="question import failed") as caught:
        run_question_import_job(
            context=context,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=exploding_importer,
        )

    assert "super-secret" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_question_import_job_honours_cancellation_before_import(tmp_path: Path) -> None:
    service, request = _service_and_request(tmp_path)
    context, store = _context(tmp_path, {"request_id": request.request_id})
    assert store.request_cancel(context.job_id)

    with pytest.raises(JobCancellationRequested):
        run_question_import_job(
            context=context,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=lambda *_args, **_kwargs: pytest.fail("importer must not run"),
        )

    assert not service.db_path.exists()


def test_question_import_job_repeat_uses_existing_questions_without_duplicates(
    tmp_path: Path,
) -> None:
    service, request = _service_and_request(tmp_path)

    def idempotent_importer(scanned, database_path, **kwargs):
        initialize_database(database_path)
        source = str(scanned[0].source_file)
        with connect(database_path) as conn:
            existing = conn.execute(
                "SELECT id FROM papers WHERE source_file = ?", (source,)
            ).fetchone()
        if existing is not None:
            return BatchImportResult(
                [PaperImportFileResult(source, "duplicate")], 0, 0, 0, 0, 1, 0
            )
        return _successful_importer(scanned, database_path, **kwargs)

    first, _ = _context(tmp_path / "first", {"request_id": request.request_id})
    second, _ = _context(tmp_path / "second", {"request_id": request.request_id})
    first_result = run_question_import_job(
        context=first,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=idempotent_importer,
    )
    second_result = run_question_import_job(
        context=second,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=idempotent_importer,
    )

    with connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 2
    assert first_result["successful_question_ids"] == [1, 2]
    assert second_result["successful_question_ids"] == [1, 2]
