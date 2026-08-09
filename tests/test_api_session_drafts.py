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
    assert body["curriculum_volume_id"] is None
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


def test_renaming_session_returns_the_safe_summary_shape(tmp_path) -> None:
    client, db, _upload_config_dir = _client_with_db(tmp_path)
    session_id = db.create_grading_session("Original", "rubric.json", "answers.json")

    renamed = client.patch(
        f"/api/sessions/{session_id}",
        json={"name": "Renamed after creation"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed after creation"
    assert set(renamed.json()) == {
        "id",
        "name",
        "status",
        "curriculum_volume_id",
        "is_deleted",
        "deleted_at",
        "created_at",
        "updated_at",
    }


def test_session_names_are_exclusive_for_create_and_rename(tmp_path) -> None:
    client, db, _upload_config_dir = _client_with_db(tmp_path)
    first = client.post("/api/sessions/drafts", json={"name": " 0526test "})
    assert first.status_code == 201

    duplicate = client.post("/api/sessions/drafts", json={"name": "0526TEST"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "session_name_conflict"
    assert duplicate.json()["error"]["message"] == "已存在同名考试，请换一个名称。"

    other_id = db.create_grading_session("另一场考试", "r.json", "a.json")
    renamed = client.patch(
        f"/api/sessions/{other_id}",
        json={"name": "0526test"},
    )
    assert renamed.status_code == 409
    assert renamed.json()["error"]["code"] == "session_name_conflict"
    assert renamed.json()["error"]["message"] == "已存在同名考试，请换一个名称。"


def test_session_draft_curriculum_volume_round_trips_and_can_be_cleared(
    tmp_path,
) -> None:
    from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

    volume_id = str(load_curriculum_catalog()["volumes"][0]["id"])
    client, db, _upload_config_dir = _client_with_db(tmp_path)

    created = client.post(
        "/api/sessions/drafts",
        json={"name": "按学期归类的考试", "curriculum_volume_id": volume_id},
    )
    assert created.status_code == 201
    assert created.json()["curriculum_volume_id"] == volume_id
    session_id = int(created.json()["id"])
    assert db.get_grading_session(session_id)["curriculum_volume_id"] == volume_id

    cleared = client.patch(
        f"/api/sessions/{session_id}",
        json={"curriculum_volume_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["curriculum_volume_id"] is None
    assert db.get_grading_session(session_id)["curriculum_volume_id"] is None
