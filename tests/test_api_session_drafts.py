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
