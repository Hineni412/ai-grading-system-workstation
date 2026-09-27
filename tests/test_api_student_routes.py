from __future__ import annotations

import pytest
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


def test_student_workspace_and_import_commit_share_revision_contract(tmp_path) -> None:
    from db_manager import StudentRecord

    client, db = _client_with_db(tmp_path)
    db.upsert_students([StudentRecord("S001", "旧姓名", "一班")])

    workspace = client.get(
        "/api/students/workspace?search=旧姓名&class_name=一班&page=1&page_size=50"
    )

    assert workspace.status_code == 200
    workspace_payload = workspace.json()
    assert workspace_payload["total"] == 1
    assert workspace_payload["page"] == 1
    assert workspace_payload["class_names"] == ["一班"]
    assert len(workspace_payload["roster_revision"]) == 64

    preview = client.post(
        "/api/students/import/preview?filename=students.csv",
        content=(
            "student_code,name,class_name\nS001,新姓名,二班\nS002,匿名学生乙,二班\n"
        ).encode(),
    ).json()
    selected = [
        {
            "student_code": row["student_code"],
            "name": row["name"],
            "class_name": row["class_name"],
        }
        for row in preview["rows"]
        if row["selectable"]
    ]
    committed = client.post(
        "/api/students/import/commit",
        json={
            "expected_revision": preview["roster_revision"],
            "items": selected,
        },
    )

    assert committed.status_code == 200
    assert committed.json()["inserted"] == 1
    assert committed.json()["updated"] == 1
    repeated = client.post(
        "/api/students/import/commit",
        json={
            "expected_revision": preview["roster_revision"],
            "items": selected,
        },
        headers={"x-request-id": "rid-student-conflict"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "student_roster_conflict"
    assert repeated.json()["error"]["request_id"] == "rid-student-conflict"


def test_student_delete_requires_impact_revision_and_confirmation(tmp_path) -> None:
    from db_manager import StudentRecord

    client, db = _client_with_db(tmp_path)
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = db.list_students()[0]["id"]

    impact = client.get(f"/api/students/{student_id}/deletion-impact")
    unconfirmed = client.delete(
        f"/api/students/{student_id}",
        params={
            "expected_revision": impact.json()["roster_revision"],
            "confirmed": "false",
        },
        headers={"x-request-id": "rid-delete-unconfirmed"},
    )

    assert impact.status_code == 200
    assert impact.json()["counts"]["deleted_students"] == 1
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["error"]["code"] == "student_roster_invalid"
    assert db.list_students()[0]["student_code"] == "S001"


@pytest.mark.parametrize("run_state", ["running", "pause_requested", "paused"])
def test_student_delete_is_rejected_while_grading_is_active(
    tmp_path,
    run_state: str,
) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db = _client_with_db(tmp_path)
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session(
        "Active grading", "rubric.json", "answer.json"
    )
    run_store = GradingRunStore(db.db_path)
    run = run_store.begin(session_id, "a" * 64, "full_paper")
    run_store.add_item(
        run.id,
        source_label="001",
        student_id=student_id,
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="grading",
    )
    if run_state == "pause_requested":
        assert run_store.request_pause(session_id) is True
    elif run_state == "paused":
        run_store.finish(run.run_token, "paused")
    impact = client.get(f"/api/students/{student_id}/deletion-impact").json()

    response = client.delete(
        f"/api/students/{student_id}",
        params={
            "expected_revision": impact["roster_revision"],
            "confirmed": "true",
        },
        headers={"x-request-id": "rid-delete-active-grading"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "student_grading_active",
            "message": "Wait for grading to finish before deleting this student",
            "details": {},
            "request_id": "rid-delete-active-grading",
        }
    }
    assert db.list_students()[0]["student_code"] == "S001"
    assert run_store.get_run(run.id).state == run_state
    assert run_store.counts(run.id)["grading"] == 1
    assert list(db.backup_dir.glob("grading_before_delete_student_*.db")) == []
