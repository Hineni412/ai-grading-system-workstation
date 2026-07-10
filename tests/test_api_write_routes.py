from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def _client_with_db(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
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

    delete_response = client.delete(f"/api/sessions/{session_id}")

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

    update_response = client.patch(
        f"/api/students/{alice_id}",
        json={"student_code": "S001", "name": "Alice Zhang", "class_name": "Class 9"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Alice Zhang"
    assert update_response.json()["class_name"] == "Class 9"

    conflict_response = client.patch(
        f"/api/students/{alice_id}",
        json={"student_code": "S002", "name": "Alice Zhang", "class_name": "Class 9"},
        headers={"x-request-id": "rid-student-conflict"},
    )

    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "student_code_conflict"
    assert conflict_response.json()["error"]["request_id"] == "rid-student-conflict"

    delete_response = client.delete(f"/api/students/{bob_id}")

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_students"] == 1
    remaining_codes = [item["student_code"] for item in client.get("/api/students").json()["items"]]
    assert remaining_codes == ["S001"]
    assert (db.backup_dir).exists()


def test_missing_student_write_route_uses_unified_404_error(tmp_path) -> None:
    client, _db = _client_with_db(tmp_path)

    response = client.patch(
        "/api/students/404",
        json={"student_code": "S404", "name": "Nobody", "class_name": None},
        headers={"x-request-id": "rid-student-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "student_not_found"
    assert response.json()["error"]["request_id"] == "rid-student-missing"
