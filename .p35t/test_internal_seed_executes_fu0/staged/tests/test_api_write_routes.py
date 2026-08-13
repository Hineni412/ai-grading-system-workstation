from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def _client_with_db(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_data_root,
        get_grading_db,
        get_question_bank_db_path,
    )
    from db_manager import DBManager
    from question_bank.database.schema import initialize_database

    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading.db")
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
    assert db.get_grading_session(session_id)["session_name"] == "Exam B"

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


def test_session_write_routes_validate_blank_name(tmp_path) -> None:
    client, _db = _client_with_db(tmp_path)

    response = client.post(
        "/api/sessions",
        json={"name": " ", "rubric_path": "r.json", "answer_key_path": "a.json"},
        headers={"x-request-id": "rid-session-invalid"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["request_id"] == "rid-session-invalid"


def test_student_write_routes_upsert_update_conflict_and_delete(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)

    upsert_response = client.post(
        "/api/students",
        json={
            "items": [
                {"student_code": "S001", "name": "Alice", "class_name": "Class 1"},
                {"student_code": "S002", "name": "Bob", "class_name": "Class 2"},
            ]
        },
    )

    assert upsert_response.status_code == 201
    assert upsert_response.json()["inserted"] == 2
    students = client.get("/api/students").json()["items"]
    alice_id = students[0]["id"]
    bob_id = students[1]["id"]
    roster_revision = client.get("/api/students/workspace").json()["roster_revision"]

    update_response = client.patch(
        f"/api/students/{alice_id}",
        json={
            "expected_revision": roster_revision,
            "student_code": "S001",
            "name": "Alice Zhang",
            "class_name": "Class 9",
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["student"]["name"] == "Alice Zhang"
    assert update_response.json()["student"]["class_name"] == "Class 9"
    updated_revision = update_response.json()["roster_revision"]

    conflict_response = client.patch(
        f"/api/students/{alice_id}",
        json={
            "expected_revision": updated_revision,
            "student_code": "S002",
            "name": "Alice Zhang",
            "class_name": "Class 9",
        },
        headers={"x-request-id": "rid-student-conflict"},
    )

    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "student_code_conflict"
    assert conflict_response.json()["error"]["request_id"] == "rid-student-conflict"

    impact_response = client.get(f"/api/students/{bob_id}/deletion-impact")
    assert impact_response.status_code == 200
    assert impact_response.json()["counts"]["deleted_students"] == 1
    delete_response = client.delete(
        f"/api/students/{bob_id}",
        params={
            "expected_revision": impact_response.json()["roster_revision"],
            "confirmed": "true",
        },
    )

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_students"] == 1
    assert delete_response.json()["backup_created"] is True
    remaining_codes = [item["student_code"] for item in client.get("/api/students").json()["items"]]
    assert remaining_codes == ["S001"]
    assert (db.backup_dir).exists()


def test_missing_student_write_route_uses_unified_404_error(tmp_path) -> None:
    client, _db = _client_with_db(tmp_path)
    roster_revision = client.get("/api/students/workspace").json()["roster_revision"]

    response = client.patch(
        "/api/students/404",
        json={
            "expected_revision": roster_revision,
            "student_code": "S404",
            "name": "Nobody",
            "class_name": None,
        },
        headers={"x-request-id": "rid-student-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "student_not_found"
    assert response.json()["error"]["request_id"] == "rid-student-missing"
