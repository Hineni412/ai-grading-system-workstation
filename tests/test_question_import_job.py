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
                1,
                2,
                0,
                0,
                0,
                0,
            )
        second_entered.set()
        return BatchImportResult(
            [PaperImportFileResult(source, "duplicate")],
            0,
            0,
            0,
            0,
            1,
            0,
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
