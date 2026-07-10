from __future__ import annotations

import json
import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def _valid_config_payload() -> dict:
    scores = [17, 17, 17, 17, 17, 15]
    rubric_questions = []
    answer_questions = []
    for index, score in enumerate(scores, start=1):
        question_id = f"Q{index}"
        part_id = f"{question_id}-P1"
        rubric_questions.append(
            {
                "question_id": question_id,
                "question_type": "comprehensive",
                "max_score": score,
                "knowledge_id": f"K{index}",
                "parts": [
                    {
                        "part_id": part_id,
                        "part_score": score,
                        "steps": [
                            {
                                "step_id": f"{part_id}-S1",
                                "step_score": score,
                                "core_goal": "answer correctly",
                                "required_elements": ["valid reasoning"],
                                "allow_alternative_methods": True,
                            }
                        ],
                    }
                ],
            }
        )
        answer_questions.append(
            {
                "question_id": question_id,
                "canonical_answer": f"Answer {index}",
                "accepted_forms": [f"Answer {index}"],
                "method_variants": [],
                "parts": [
                    {
                        "part_id": part_id,
                        "answer": f"Answer {index}",
                        "analysis": "analysis",
                        "step_milestones": ["valid reasoning"],
                    }
                ],
            }
        )
    return {
        "rubric": {
            "exam_title": "Contract Exam",
            "total_score": 100,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {"warnings": []},
    }


def _client_with_db(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_upload_config_dir,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()

    app = create_app()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_upload_config_dir] = lambda: tmp_path / "uploaded"
    return TestClient(app), db


def _write_initial_config(tmp_path: Path, db, payload: dict) -> int:
    config_dir = tmp_path / "initial"
    config_dir.mkdir()
    rubric_path = config_dir / "rubric.json"
    answer_key_path = config_dir / "answer_key.json"
    rubric_path.write_text(json.dumps(payload["rubric"], ensure_ascii=False), encoding="utf-8")
    answer_key_path.write_text(json.dumps(payload["answer_key"], ensure_ascii=False), encoding="utf-8")
    return db.create_grading_session(
        "Config Exam",
        str(rubric_path),
        str(answer_key_path),
    )


def test_session_config_route_reads_existing_json_files(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    payload = _valid_config_payload()
    session_id = _write_initial_config(tmp_path, db, payload)

    response = client.get(f"/api/sessions/{session_id}/config")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["rubric"]["exam_title"] == "Contract Exam"
    assert len(body["answer_key"]["questions"]) == 6


def test_session_config_route_saves_payload_and_updates_session_paths(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    payload = _valid_config_payload()
    session_id = _write_initial_config(tmp_path, db, payload)
    payload["rubric"]["exam_title"] = "Updated Exam"

    response = client.put(f"/api/sessions/{session_id}/config", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["rubric"]["exam_title"] == "Updated Exam"
    assert Path(body["rubric_path"]).exists()
    assert Path(body["answer_key_path"]).exists()
    assert str(tmp_path / "uploaded") in body["rubric_path"]
    session = db.get_grading_session(session_id)
    assert session["rubric_path"] == body["rubric_path"]
    assert session["answer_key_path"] == body["answer_key_path"]


def test_session_config_route_rejects_invalid_payload_with_unified_error(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _write_initial_config(tmp_path, db, _valid_config_payload())

    response = client.put(
        f"/api/sessions/{session_id}/config",
        json={"rubric": {"questions": []}, "answer_key": {"questions": []}, "meta": {"warnings": []}},
        headers={"x-request-id": "rid-invalid-config"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_config"
    assert response.json()["error"]["request_id"] == "rid-invalid-config"


def test_missing_session_config_route_uses_unified_404_error(tmp_path) -> None:
    client, _db = _client_with_db(tmp_path)

    response = client.get(
        "/api/sessions/404/config",
        headers={"x-request-id": "rid-missing-config"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-missing-config"


def test_missing_config_file_error_does_not_expose_stored_absolute_path(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    missing_path = tmp_path / "private" / "missing-rubric.json"
    session_id = db.create_grading_session(
        "Missing config",
        str(missing_path),
        str(tmp_path / "private" / "answer-key.json"),
    )

    response = client.get(f"/api/sessions/{session_id}/config")

    assert response.status_code == 404
    assert response.json()["error"]["details"] == {"field": "rubric_path"}
    assert str(missing_path) not in response.text


def test_invalid_json_config_error_does_not_expose_stored_absolute_path(tmp_path) -> None:
    client, db = _client_with_db(tmp_path)
    invalid_path = tmp_path / "private" / "invalid-rubric.json"
    invalid_path.parent.mkdir(parents=True)
    invalid_path.write_text("{not-json", encoding="utf-8")
    session_id = db.create_grading_session(
        "Invalid config",
        str(invalid_path),
        str(tmp_path / "private" / "answer-key.json"),
    )

    response = client.get(f"/api/sessions/{session_id}/config")

    assert response.status_code == 500
    assert response.json()["error"]["details"] == {"field": "rubric_path"}
    assert str(invalid_path) not in response.text
