from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories


def _client_with_db(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_data_root,
        get_grading_db,
        get_question_bank_db_path,
    )
    
    from question_bank.database.schema import initialize_database

    data_root = tmp_path / "user_data"
    db = open_grading_repositories(data_root / "databases" / "grading.db")
    db.initialize()
    question_bank_db = data_root / "databases" / "question_bank.db"
    initialize_database(question_bank_db)

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_data_root] = lambda: data_root
    app.dependency_overrides[get_question_bank_db_path] = lambda: question_bank_db
    return TestClient(app), db


def test_session_write_routes_create_rename_soft_delete_and_restore(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)

    create_response = client.post(
        "/api/sessions",
        json={
            "name": "Exam B",
            "rubric_path": "rubric-b.json",
            "answer_key_path": "answer-b.json",
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    session_id = created["id"]
    assert created["name"] == "Exam B"
    assert created["rubric_path"] == "rubric-b.json"
    assert db.sessions.get_grading_session(session_id)["session_name"] == "Exam B"

    rename_response = client.patch(
        f"/api/sessions/{session_id}",
        json={"name": "Exam B Renamed"},
    )

    assert rename_response.status_code == 200
    assert rename_response.json()["name"] == "Exam B Renamed"

    impact_response = client.get(f"/api/sessions/{session_id}/deletion-impact")
    assert impact_response.status_code == 200
    delete_response = client.request(
        "DELETE",
        f"/api/sessions/{session_id}",
        json={
            "expected_revision": impact_response.json()["revision"],
            "confirmation_name": "Exam B Renamed",
        },
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["is_deleted"] is True
    assert client.get("/api/sessions").json()["total"] == 0
    assert client.get("/api/sessions?include_deleted=true").json()["total"] == 1

    restore_response = client.post(f"/api/sessions/{session_id}/restore")

    assert restore_response.status_code == 200
    assert restore_response.json()["is_deleted"] is False
    assert client.get("/api/sessions").json()["items"][0]["id"] == session_id
