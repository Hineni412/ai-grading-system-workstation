from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def test_batch_a_routes_are_registered_in_openapi() -> None:
    from backend.api.app import create_app

    client = TestClient(create_app())

    paths = client.get("/api/openapi.json").json()["paths"]

    assert "/api/sessions" in paths
    assert "/api/sessions/{session_id}" in paths
    assert "/api/sessions/{session_id}/progress" in paths
    assert "/api/sessions/{session_id}/template" in paths
    assert "/api/sessions/{session_id}/regions" in paths
    assert "/api/students" in paths


def test_sessions_routes_return_existing_db_state(tmp_path) -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager, StudentRecord

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = db.list_students()[0]["id"]
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    template_id = db.upsert_session_template(session_id, "front.png", "back.png")
    db.add_answer_region(
        session_id,
        template_id,
        {
            "page": "front",
            "region_order": 1,
            "x": 10,
            "y": 20,
            "w": 300,
            "h": 120,
            "mapped_question_id": "Q1",
            "is_confirmed": True,
        },
    )
    db.mark_template_confirmed(session_id, True)
    db.create_exam_paper(
        session_id,
        "front-paper.png",
        "back-paper.png",
        "Alice",
        student_id,
        "matched",
        "failed",
        "Request timed out.",
    )

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)

    sessions = client.get("/api/sessions").json()
    assert sessions["total"] == 1
    assert sessions["items"][0]["id"] == session_id
    assert sessions["items"][0]["name"] == "Exam A"

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["id"] == session_id
    assert detail["rubric_path"] == "rubric.json"

    progress = client.get(f"/api/sessions/{session_id}/progress").json()
    assert progress["total_papers"] == 1
    assert progress["failed_papers"] == 1
    assert progress["progress_percent"] == 100.0

    template = client.get(f"/api/sessions/{session_id}/template").json()
    assert template["id"] == template_id
    assert template["is_confirmed"] is True

    regions = client.get(f"/api/sessions/{session_id}/regions").json()
    assert regions["total"] == 1
    assert regions["items"][0]["mapped_question_id"] == "Q1"


def test_students_route_returns_students_sorted_by_db(tmp_path) -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager, StudentRecord

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    db.upsert_students(
        [
            StudentRecord("S002", "Bob", "Class 2"),
            StudentRecord("S001", "Alice", "Class 1"),
        ]
    )

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)

    response = client.get("/api/students")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["student_code"] for item in payload["items"]] == ["S001", "S002"]


def test_missing_session_routes_use_unified_404_error(tmp_path) -> None:
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    client = TestClient(app)

    response = client.get("/api/sessions/404", headers={"x-request-id": "rid-missing"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-missing"
