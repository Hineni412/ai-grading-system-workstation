from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_job_manager,
    get_question_bank_read_service,
)
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.database.schema import initialize_database
from question_bank.services.question_read_service import QuestionBankReadService


def _seed_database(db_path: Path) -> None:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, '试卷甲', 'success')"
        )
        conn.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (2, '试卷乙', 'success')"
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, is_deleted
            ) VALUES (?, 1, ?, ?, 0)
            """,
            [(101, "2", "第二题"), (102, "1", "第一题"), (103, "x", "非数字题")],
        )
        conn.execute(
            "INSERT INTO questions (id, paper_id, question_number, question_text, is_deleted) "
            "VALUES (104, 1, '9', '已删除题', 1)"
        )


def _perf_client(tmp_path: Path) -> tuple[TestClient, QuestionBankReadService, JobManager]:
    db_path = tmp_path / "question_bank.db"
    _seed_database(db_path)
    read_service = QuestionBankReadService(db_path)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_question_bank_read_service] = lambda: read_service
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app), read_service, manager


def test_question_refs_returns_slim_sorted_rows(tmp_path: Path) -> None:
    client, _, _ = _perf_client(tmp_path)

    response = client.get(
        "/api/question-bank/question-refs",
        params={"paper_ids": [1, 2], "analysis_status": "incomplete"},
    )

    assert response.status_code == 200
    body = response.json()
    # Exact slim contract: no rich content, tags or timestamps leak through.
    assert set(body) == {"items", "total", "page", "page_size", "total_pages"}
    assert body["total"] == 3
    assert body["page_size"] == 500
    assert body["items"] == [
        {"id": 102, "paper_id": 1, "question_number": "1"},
        {"id": 101, "paper_id": 1, "question_number": "2"},
        {"id": 103, "paper_id": 1, "question_number": "x"},
    ]
    for item in body["items"]:
        assert set(item) == {"id", "paper_id", "question_number"}


def test_question_refs_pagination(tmp_path: Path) -> None:
    client, _, _ = _perf_client(tmp_path)

    first = client.get(
        "/api/question-bank/question-refs",
        params={"page": 1, "page_size": 2},
    ).json()
    second = client.get(
        "/api/question-bank/question-refs",
        params={"page": 2, "page_size": 2},
    ).json()

    assert first["total"] == 3
    assert [item["id"] for item in first["items"]] == [102, 101]
    assert second["total_pages"] == 2
    assert [item["id"] for item in second["items"]] == [103]


def test_question_refs_rejects_out_of_range_page_size(tmp_path: Path) -> None:
    client, _, _ = _perf_client(tmp_path)

    response = client.get(
        "/api/question-bank/question-refs",
        params={"page_size": 501},
    )

    assert response.status_code == 422


def test_job_status_batch_returns_matching_shapes(tmp_path: Path) -> None:
    client, _, manager = _perf_client(tmp_path)
    store = manager.store
    created = store.create_job("tagging_sync", {"question_ids": [1]})
    missing_id = created.id + 999

    response = client.post(
        "/api/jobs/status-batch",
        json={"ids": [created.id, missing_id]},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [created.id, missing_id]
    found = body["items"][0]
    assert found["found"] is True
    assert found["job"]["id"] == created.id
    assert found["job"]["job_type"] == "tagging_sync"
    # The per-job shape must match GET /jobs/{id} so the client cannot tell
    # the difference between batched and single polling.
    single = client.get(f"/api/jobs/{created.id}").json()
    assert found["job"] == single
    missing = body["items"][1]
    assert missing["found"] is False
    assert missing["job"] is None


def test_job_status_batch_validates_ids(tmp_path: Path) -> None:
    client, _, _ = _perf_client(tmp_path)

    empty = client.post("/api/jobs/status-batch", json={"ids": []})
    oversized = client.post(
        "/api/jobs/status-batch",
        json={"ids": list(range(1, 52))},
    )

    assert empty.status_code == 422
    assert oversized.status_code == 422
