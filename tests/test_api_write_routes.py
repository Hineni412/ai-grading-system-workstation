from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories
import pytest


def _originals_client(tmp_path, *, review_pending=False):
    from types import SimpleNamespace
    from backend.api.app import create_app
    from backend.api.dependencies import (get_data_root, get_grading_db, get_scan_grading_workspace,
        get_review_application_service, get_ops_self_check_service, get_media_service)
    from backend.scan_grading.workspace import ScanGradingWorkspace
    from tests.test_review_media_service import _seed_media
    seed = _seed_media(tmp_path)
    app = create_app()
    workspace = ScanGradingWorkspace(exams_root=seed.exams_dir, templates_root=seed.templates_dir, grading_db_path=seed.db.db_path, data_root=seed.data_root)
    app.dependency_overrides[get_data_root] = lambda: seed.data_root
    app.dependency_overrides[get_grading_db] = lambda: seed.db
    app.dependency_overrides[get_scan_grading_workspace] = lambda: workspace
    app.dependency_overrides[get_review_application_service] = lambda: SimpleNamespace(list_questions=lambda *args, **kwargs: [SimpleNamespace(needs_review_count=int(review_pending), ungraded_count=0, failed_count=0)])
    app.dependency_overrides[get_ops_self_check_service] = lambda: SimpleNamespace(list_backups=lambda limit: {"items": []})
    app.dependency_overrides[get_media_service] = lambda: seed.service
    return TestClient(app), seed, workspace


@pytest.mark.parametrize("condition,reason", [("active", "这场考试还有正在运行的任务"), ("review", "复核完成后可清理"), ("unmatched", "还有答卷没有对应到学生"), ("empty", "还没有批改结果"), ("replacement", "正在替换答卷，完成后再清理")])
def test_originals_preview_disables_ineligible_exam(tmp_path, monkeypatch, condition, reason):
    import sqlite3
    client, seed, workspace = _originals_client(tmp_path, review_pending=condition == "review")
    if condition == "active":
        original = seed.db.sessions.session_deletion_impact
        monkeypatch.setattr(seed.db.sessions, "session_deletion_impact", lambda sid: {**original(sid), "active_jobs": 1})
    elif condition in {"unmatched", "empty"}:
        with sqlite3.connect(seed.db.db_path) as conn:
            conn.execute("UPDATE exam_papers SET match_status='unmatched'" if condition == "unmatched" else "DELETE FROM session_results")
    elif condition == "replacement":
        path = workspace._replacement_manifest_path(seed.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    response = client.get(f"/api/sessions/{seed.session_id}/originals")
    assert response.status_code == 200
    assert response.json()["blocked_reason"] == reason
    assert not response.json()["can_clear"]


def test_originals_confirmation_revision_and_resume(tmp_path):
    from backend.files.session_originals import receipt_path
    client, seed, _ = _originals_client(tmp_path)
    url = f"/api/sessions/{seed.session_id}/originals"
    preview = client.get(url).json()
    assert preview["can_clear"]
    assert client.post(url + "/clear", json={"expected_revision": preview["revision"], "confirmation_phrase": "清除"}).status_code == 422
    conflict = client.post(url + "/clear", json={"expected_revision": "old", "confirmation_phrase": "确认清除"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "originals_revision_changed"
    receipt_path(seed.data_root, seed.session_id).write_text("broken", encoding="utf-8")
    completed = client.post(url + "/clear", json={"expected_revision": preview["revision"], "confirmation_phrase": "确认清除"})
    assert completed.status_code == 200 and completed.json()["originals_state"] == "cleared"


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
