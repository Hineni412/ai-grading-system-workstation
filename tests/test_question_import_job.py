from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

import backend.jobs.question_import as question_import_module
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

    forged_id = "a" * 32
    assert forged_id != request.request_id
    forged_payload = request.to_dict()
    forged_payload["request_id"] = forged_id
    forged_path = (
        service.data_root
        / "question_bank"
        / "import_staging"
        / "requests"
        / f"{forged_id}.json"
    )
    forged_path.write_text(
        json.dumps(forged_payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(QuestionImportUploadNotFound):
        service.load_import_resource(forged_id)

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


def test_question_import_job_restores_safe_filename_metadata_inference(
    tmp_path: Path,
) -> None:
    service = QuestionBankWriteService(
        tmp_path / "data" / "databases" / "question_bank.db",
        data_root=tmp_path / "data",
    )
    upload = service.stage_upload(
        filename="2025广东省深圳市九年级下学期期末试卷.docx",
        content=b"fake-docx",
    )
    request = service.create_import_request(upload_id=upload.upload_id)
    context, _store = _context(tmp_path, {"request_id": request.request_id})
    captured = {}

    def importer(scanned, database_path, **_kwargs):
        captured["metadata"] = scanned[0].metadata
        return _successful_importer(scanned, database_path, **_kwargs)

    run_question_import_job(
        context=context,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=importer,
    )

    metadata = captured["metadata"]
    assert metadata.year == "2025"
    assert metadata.province == "广东省"
    assert metadata.city == "深圳市"
    assert metadata.exam_type == "期末"
    assert metadata.grade == "九年级"
    assert metadata.semester == "下学期"


@pytest.mark.parametrize(
    ("filename", "expected_year", "expected_exam_type"),
    [
        ("0526test2.docx", "2026", "阶段练习"),
        ("2025期末练习.docx", "2025", "期末"),
    ],
)
def test_question_import_job_keeps_original_title_and_uses_sync_metadata_defaults(
    tmp_path: Path,
    filename: str,
    expected_year: str,
    expected_exam_type: str,
) -> None:
    service = QuestionBankWriteService(
        tmp_path / "data" / "databases" / "question_bank.db",
        data_root=tmp_path / "data",
    )
    upload = service.stage_upload(
        filename=filename,
        content=b"fake-docx",
    )
    request = service.create_import_request(upload_id=upload.upload_id)
    context, _store = _context(
        tmp_path,
        {
            "request_id": request.request_id,
            "paper_defaults": {
                "year": "2026",
                "exam_type": "阶段练习",
            },
        },
    )
    captured = {}

    def importer(scanned, database_path, **_kwargs):
        captured["paper"] = scanned[0]
        return _successful_importer(scanned, database_path, **_kwargs)

    run_question_import_job(
        context=context,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=importer,
    )

    imported = captured["paper"]
    assert imported.title == Path(filename).stem
    assert imported.metadata.year == expected_year
    assert imported.metadata.exam_type == expected_exam_type


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


def test_question_import_job_honours_cancellation_requested_during_import(
    tmp_path: Path,
) -> None:
    service, request = _service_and_request(tmp_path)
    context, store = _context(tmp_path, {"request_id": request.request_id})

    def cancelling_importer(scanned, database_path, **kwargs):
        result = _successful_importer(scanned, database_path, **kwargs)
        assert store.request_cancel(context.job_id)
        return result

    with pytest.raises(JobCancellationRequested):
        run_question_import_job(
            context=context,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=cancelling_importer,
        )


def test_question_import_job_serializes_same_request_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, request = _service_and_request(tmp_path)
    initialize_database(service.db_path)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("CREATE TABLE import_effects (id INTEGER PRIMARY KEY)")
        conn.commit()
    first, _ = _context(tmp_path / "first", {"request_id": request.request_id})
    second, _ = _context(tmp_path / "second", {"request_id": request.request_id})
    entered = threading.Event()
    second_waiting = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    guard = threading.Lock()
    lock_attempts = 0
    real_locks = question_import_module.keyed_execution_locks

    @contextmanager
    def observed_locks(keys, **kwargs):
        nonlocal lock_attempts
        with guard:
            lock_attempts += 1
            if lock_attempts == 2:
                second_waiting.set()
        with real_locks(keys, **kwargs):
            yield

    monkeypatch.setattr(question_import_module, "keyed_execution_locks", observed_locks)

    def blocking_importer(scanned, _database_path, **_kwargs):
        with guard:
            calls.append(len(calls) + 1)
            call_number = len(calls)
        with sqlite3.connect(service.db_path) as conn:
            existing = int(conn.execute("SELECT COUNT(*) FROM import_effects").fetchone()[0])
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=5)
        if existing == 0:
            with sqlite3.connect(service.db_path) as conn:
                conn.execute("INSERT INTO import_effects DEFAULT VALUES")
                conn.commit()
        return BatchImportResult(
            [], 0, 0, 0, 0, 1, 0
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            run_question_import_job,
            context=first,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=blocking_importer,
        )
        assert entered.wait(timeout=5)
        second_future = executor.submit(
            run_question_import_job,
            context=second,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=blocking_importer,
        )
        assert second_waiting.wait(timeout=5)
        release.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    assert calls == [1, 2]
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM import_effects").fetchone()[0] == 1


def test_question_import_job_cancels_while_waiting_for_same_request_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, request = _service_and_request(tmp_path)
    first, _ = _context(tmp_path / "first", {"request_id": request.request_id})
    second, second_store = _context(
        tmp_path / "second", {"request_id": request.request_id}
    )
    holder_entered = threading.Event()
    second_waiting = threading.Event()
    release = threading.Event()
    lock_attempts = 0
    guard = threading.Lock()
    real_locks = question_import_module.keyed_execution_locks

    @contextmanager
    def observed_locks(keys, **kwargs):
        nonlocal lock_attempts
        with guard:
            lock_attempts += 1
            if lock_attempts == 2:
                second_waiting.set()
        with real_locks(keys, **kwargs):
            yield

    monkeypatch.setattr(question_import_module, "keyed_execution_locks", observed_locks)

    def blocking_importer(*_args, **_kwargs):
        holder_entered.set()
        assert release.wait(timeout=5)
        return BatchImportResult([], 0, 0, 0, 0, 0, 0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            run_question_import_job,
            context=first,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=blocking_importer,
        )
        assert holder_entered.wait(timeout=5)
        second_future = executor.submit(
            run_question_import_job,
            context=second,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=blocking_importer,
        )
        assert second_waiting.wait(timeout=5)
        assert second_store.request_cancel(second.job_id)
        try:
            with pytest.raises(JobCancellationRequested):
                second_future.result(timeout=1)
        finally:
            release.set()
        first_future.result(timeout=5)


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


def test_question_import_job_new_upload_of_same_content_reuses_fingerprinted_paper(
    tmp_path: Path,
) -> None:
    service, first_request = _service_and_request(tmp_path)
    fingerprint = hashlib.sha256(b"fake-docx").hexdigest()
    initialize_database(service.db_path)
    with connect(service.db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, import_status, content_fingerprint
                ) VALUES (?, ?, ?, ?)
                """,
                ("测试试卷", "first-copy.docx", "imported", fingerprint),
            ).lastrowid
        )
        for number in ("1", "2"):
            conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_text, source_file
                ) VALUES (?, ?, ?, ?)
                """,
                (paper_id, number, f"第{number}题测试题干", "first-copy.docx"),
            )

    second_upload = service.stage_upload(
        filename="测试试卷.docx",
        content=b"fake-docx",
    )
    second_request = service.create_import_request(upload_id=second_upload.upload_id)

    def duplicate_importer(scanned, _database_path, **_kwargs):
        source = str(scanned[0].source_file)
        return BatchImportResult(
            [PaperImportFileResult(source, "duplicate")], 0, 0, 0, 0, 1, 0
        )

    context, _store = _context(
        tmp_path / "second",
        {"request_id": second_request.request_id},
    )
    result = run_question_import_job(
        context=context,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=duplicate_importer,
    )

    assert first_request.request_id != second_request.request_id
    assert result["outcome"] == "complete"
    assert result["successful_question_ids"] == [1, 2]
    with connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 2


def test_question_import_job_serializes_different_requests_for_same_content(
    tmp_path: Path,
) -> None:
    service, first_request = _service_and_request(tmp_path)
    second_upload = service.stage_upload(
        filename="测试试卷副本.docx",
        content=b"fake-docx",
    )
    second_request = service.create_import_request(upload_id=second_upload.upload_id)
    fingerprint = hashlib.sha256(b"fake-docx").hexdigest()
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def serialized_importer(scanned, database_path, **_kwargs):
        nonlocal call_count
        initialize_database(database_path)
        source = str(scanned[0].source_file)
        with call_lock:
            call_count += 1
            call_number = call_count
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
            with connect(database_path) as connection:
                paper_id = int(
                    connection.execute(
                        """
                        INSERT INTO papers (
                            title, source_file, import_status,
                            content_fingerprint
                        ) VALUES (?, ?, ?, ?)
                        """,
                        ("测试试卷", source, "imported", fingerprint),
                    ).lastrowid
                )
                for number in ("1", "2"):
                    connection.execute(
                        """
                        INSERT INTO questions (
                            paper_id, question_number, question_text,
                            source_file
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (paper_id, number, f"第{number}题测试题干", source),
                    )
            return BatchImportResult(
                [PaperImportFileResult(source, "imported", question_count=2)],
                1, 2, 0, 0, 0, 0,
            )
        second_entered.set()
        return BatchImportResult(
            [PaperImportFileResult(source, "duplicate")],
            0, 0, 0, 0, 1, 0,
        )

    first_context, _ = _context(
        tmp_path / "first",
        {"request_id": first_request.request_id},
    )
    second_context, _ = _context(
        tmp_path / "second",
        {"request_id": second_request.request_id},
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            run_question_import_job,
            context=first_context,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=serialized_importer,
        )
        assert first_entered.wait(timeout=5)
        second_future = executor.submit(
            run_question_import_job,
            context=second_context,
            question_bank_db_path=service.db_path,
            data_root=service.data_root,
            write_service=service,
            importer=serialized_importer,
        )
        assert not second_entered.wait(timeout=0.2)
        release_first.set()
        first_result = first_future.result(timeout=5)
        second_result = second_future.result(timeout=5)

    assert first_result["successful_question_ids"] == [1, 2]
    assert second_result["successful_question_ids"] == [1, 2]
    with connect(service.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 2
