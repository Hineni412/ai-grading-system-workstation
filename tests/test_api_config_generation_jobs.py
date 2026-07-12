from __future__ import annotations

import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_grading_db, get_job_manager, get_upload_config_dir
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager


def _client(tmp_path: Path) -> tuple[TestClient, DBManager, JobManager]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register(
        "config_generation",
        lambda context: {
            "session_id": int(context.payload["session_id"]),
            "outcome": "partial",
            "total_questions": 1,
            "generated_questions": 0,
            "failed_count": 1,
            "failed_question_ids": ["Q1"],
            "retryable": True,
        },
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_upload_config_dir] = lambda: tmp_path / "uploaded"
    return TestClient(app), db, manager


def _session(db: DBManager, tmp_path: Path) -> int:
    rubric = tmp_path / "rubric.json"
    answer = tmp_path / "answer.json"
    rubric.write_text("{}", encoding="utf-8")
    answer.write_text("{}", encoding="utf-8")
    return db.create_grading_session("Config Job", str(rubric), str(answer))


def _request_payload() -> dict[str, object]:
    return {
        "confirmed_blocks": [
            {
                "question_id": "Q1",
                "question_type": "choice",
                "text": "1 + 1 = ?",
                "canonical_answer": "2",
            }
        ],
        "document_text": "private exam text that must not be returned",
        "question_images": {},
    }


def test_config_generation_route_submits_safe_queryable_job(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=_request_payload(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_type"] == "config_generation"
    assert body["payload"] == {"session_id": session_id, "mode": "generate"}
    assert "private exam text" not in response.text
    manager.wait(body["id"], timeout=5)
    queried = client.get(f"/api/jobs/{body['id']}")
    assert queried.status_code == 200
    assert queried.json()["result"]["failed_question_ids"] == ["Q1"]


def test_config_generation_route_rejects_missing_session(tmp_path: Path) -> None:
    client, _db, _manager = _client(tmp_path)

    response = client.post("/api/sessions/404/config/generate", json=_request_payload())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_config_generation_route_rejects_sensitive_or_extra_fields(tmp_path: Path) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["api_key"] = "must-not-persist"

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "must-not-persist" not in response.text


def test_config_generation_route_rejects_nested_client_file_paths(tmp_path: Path) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["confirmed_blocks"][0]["image_paths"] = ["C:/private/answer.png"]

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "C:/private/answer.png" not in response.text


def test_config_generation_route_rejects_embedded_image_file_references(
    tmp_path: Path,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["confirmed_blocks"][0]["question_html"] = '<p>题目</p><img src="secret.png">'

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "secret.png" not in response.text


def test_config_generation_retry_route_accepts_partial_source_job(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {"session_id": session_id, "mode": "generate", "input_id": "a" * 32},
    )
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": ["Q1", "Q2"],
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id, "retry_question_ids": ["Q2"]},
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "session_id": session_id,
        "mode": "retry",
        "source_job_id": source.id,
        "retry_question_ids": ["Q2"],
    }
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert stored.payload["input_id"] == "a" * 32
    assert "input_id" not in response.json()["payload"]
