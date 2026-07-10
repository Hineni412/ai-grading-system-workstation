from __future__ import annotations

import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def _client_with_db(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_job_manager, get_templates_dir
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()

    app = create_app()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_templates_dir] = lambda: tmp_path / "templates"
    return TestClient(app), db


def _session(db) -> int:
    return db.create_grading_session("Template Exam", "rubric.json", "answer.json")


def _template_files(tmp_path: Path) -> tuple[Path, Path]:
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    front.write_bytes(b"front-template")
    back.write_bytes(b"back-template")
    return front, back


def _region(region_uuid: str = "r1", *, mapped_question_id: str | None = "Q1") -> dict:
    return {
        "region_uuid": region_uuid,
        "page": "front",
        "region_order": 1,
        "x": 10,
        "y": 20,
        "w": 100,
        "h": 80,
        "mapped_question_id": mapped_question_id,
        "mapping_status": "manual" if mapped_question_id else "unbound",
        "is_confirmed": True,
    }


def _bind_template(client: TestClient, session_id: int, front: Path, back: Path) -> dict:
    response = client.put(
        f"/api/sessions/{session_id}/template",
        json={
            "front_template_path": str(front),
            "back_template_path": str(back),
            "ai_analysis_path": "analysis.json",
            "template_config_path": "mapping.json",
            "regions_path": "regions.json",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_template_route_binds_existing_template_paths(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    front, back = _template_files(tmp_path)

    body = _bind_template(client, session_id, front, back)

    assert body["session_id"] == session_id
    assert body["front_template_path"] == str(front)
    assert body["back_template_path"] == str(back)
    assert body["is_confirmed"] is False
    assert db.get_session_template(session_id)["template_config_path"] == "mapping.json"


def test_answer_region_draft_route_saves_and_loads_compatible_draft(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    front, back = _template_files(tmp_path)
    _bind_template(client, session_id, front, back)

    save_response = client.put(
        f"/api/sessions/{session_id}/regions/draft",
        json={"revision": 1, "regions": [_region()]},
    )

    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["status"] == "compatible"
    assert saved["template_fingerprint"]
    assert Path(saved["draft_path"]).exists()

    load_response = client.get(f"/api/sessions/{session_id}/regions/draft")

    assert load_response.status_code == 200
    loaded = load_response.json()
    assert loaded["status"] == "compatible"
    assert loaded["draft"]["regions"][0]["region_uuid"] == "r1"


def test_answer_region_commit_route_commits_regions_and_snapshot(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    front, back = _template_files(tmp_path)
    _bind_template(client, session_id, front, back)
    draft = client.put(
        f"/api/sessions/{session_id}/regions/draft",
        json={"revision": 1, "regions": [_region()]},
    ).json()

    response = client.post(
        f"/api/sessions/{session_id}/regions/commit",
        json={
            "regions": [_region()],
            "image_sizes": {"front": [1000, 1000], "back": [1000, 1000]},
            "template_matches": True,
            "expected_template_fingerprint": draft["template_fingerprint"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["committed"] is True
    assert body["snapshot_pending"] is False
    assert body["issues"] == []
    assert body["region_count"] == 1
    assert db.is_template_ready(session_id) is True
    saved = db.list_answer_regions(session_id)
    assert saved[0]["region_uuid"] == "r1"
    assert saved[0]["is_confirmed"] == 1


def test_answer_region_commit_route_returns_validation_issues_without_commit(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    front, back = _template_files(tmp_path)
    _bind_template(client, session_id, front, back)

    response = client.post(
        f"/api/sessions/{session_id}/regions/commit",
        json={
            "regions": [_region(mapped_question_id=None)],
            "image_sizes": {"front": [1000, 1000], "back": [1000, 1000]},
            "template_matches": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["committed"] is False
    assert body["issues"][0]["code"] == "unbound_question"
    assert db.list_answer_regions(session_id) == []


def test_answer_region_draft_route_requires_template(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)

    response = client.get(
        f"/api/sessions/{session_id}/regions/draft",
        headers={"x-request-id": "rid-template-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "template_not_found"
    assert response.json()["error"]["request_id"] == "rid-template-missing"


def test_missing_template_file_error_does_not_expose_stored_absolute_path(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    missing_front = tmp_path / "private" / "missing-front.jpg"
    back = tmp_path / "back.jpg"
    back.write_bytes(b"back-template")
    _bind_template(client, session_id, missing_front, back)

    response = client.get(f"/api/sessions/{session_id}/regions/draft")

    assert response.status_code == 404
    assert response.json()["error"]["details"] == {
        "field": "front_template_path"
    }
    assert str(missing_front) not in response.text
