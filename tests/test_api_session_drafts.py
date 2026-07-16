from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def _client_with_db(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_upload_config_dir
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    upload_config_dir = tmp_path / "uploaded"

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_upload_config_dir] = lambda: upload_config_dir
    return TestClient(app), db, upload_config_dir


def test_create_session_draft_route_returns_summary_without_paths(tmp_path) -> None:
    client, db, upload_config_dir = _client_with_db(tmp_path)

    response = client.post(
        "/api/sessions/drafts",
        json={"name": "  七年级数学期末  "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "七年级数学期末"
    assert body["status"] == "created"
    assert "rubric_path" not in body
    assert "answer_key_path" not in body
    assert "source_paper_path" not in body
    row = db.get_grading_session(body["id"])
    assert row is not None
    assert str(upload_config_dir) in row["rubric_path"]
    assert str(upload_config_dir) in row["answer_key_path"]


def test_create_session_draft_route_maps_blank_name_to_stable_400(tmp_path) -> None:
    client, _db, _upload_config_dir = _client_with_db(tmp_path)

    response = client.post(
        "/api/sessions/drafts",
        json={"name": " "},
        headers={"x-request-id": "rid-invalid-session-draft"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_session_draft"
    assert payload["error"]["request_id"] == "rid-invalid-session-draft"


def test_create_session_draft_route_rejects_client_path_fields(tmp_path) -> None:
    client, db, upload_config_dir = _client_with_db(tmp_path)

    response = client.post(
        "/api/sessions/drafts",
        json={
            "name": "Path injection",
            "rubric_path": "C:/private/rubric.json",
            "answer_key_path": "C:/private/answer.json",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert db.list_grading_sessions(include_deleted=True) == []
    assert not upload_config_dir.exists()


def test_renaming_session_draft_keeps_editor_unconfigured(tmp_path) -> None:
    client, _db, _upload_config_dir = _client_with_db(tmp_path)
    created = client.post("/api/sessions/drafts", json={"name": "Original"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    renamed = client.patch(
        f"/api/sessions/{session_id}",
        json={"name": "Renamed after creation"},
    )
    assert renamed.status_code == 200

    editor = client.get(f"/api/sessions/{session_id}/config/editor")
    assert editor.status_code == 200
    assert editor.json()["configured"] is False
