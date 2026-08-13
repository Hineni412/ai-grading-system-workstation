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


def test_student_import_preview_publishes_safe_row_diagnostics(tmp_path) -> None:
    client, _db = _client_with_db(tmp_path)

    response = client.post(
        "/api/students/import/preview?filename=students.csv",
        content=(
            "student_code,name,class_name\n"
            "S001,匿名学生甲,一班\n"
            "S002,,二班\n"
        ).encode(),
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "students.csv"
    assert payload["columns"] == ["student_code", "name", "class_name"]
    assert payload["counts"] == {
        "insert": 1,
        "update": 0,
        "unchanged": 0,
        "invalid": 1,
        "duplicate": 0,
    }
    assert payload["rows"][0]["operation"] == "insert"
    assert payload["rows"][1]["issues"] == ["学号和姓名不能为空"]
    assert len(payload["roster_revision"]) == 64
    assert "path" not in response.text.lower()


def test_student_import_preview_returns_sanitized_unreadable_error(tmp_path) -> None:
    client, _db = _client_with_db(tmp_path)

    response = client.post(
        "/api/students/import/preview?filename=students.xlsx",
        content=b"not-an-xlsx",
        headers={
            "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "x-request-id": "rid-student-preview",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "student_roster_unreadable",
            "message": "Student roster file is unreadable",
            "details": {},
            "request_id": "rid-student-preview",
        }
    }


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
            "student_code,name,class_name\n"
            "S001,新姓名,二班\n"
            "S002,匿名学生乙,二班\n"
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


def test_student_update_rejects_a_stale_roster_revision(tmp_path) -> None:
    from db_manager import StudentRecord

    client, db = _client_with_db(tmp_path)
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    workspace = client.get("/api/students/workspace").json()
    student_id = workspace["items"][0]["id"]

    first = client.patch(
        f"/api/students/{student_id}",
        json={
            "expected_revision": workspace["roster_revision"],
            "student_code": "S001",
            "name": "Alice Zhang",
            "class_name": "Class 2",
        },
    )
    stale = client.patch(
        f"/api/students/{student_id}",
        json={
            "expected_revision": workspace["roster_revision"],
            "student_code": "S001",
            "name": "Stale Name",
            "class_name": "Class 3",
        },
        headers={"x-request-id": "rid-student-stale"},
    )

    assert first.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "student_roster_conflict"
    assert stale.json()["error"]["request_id"] == "rid-student-stale"
    assert client.get("/api/students").json()["items"][0]["name"] == "Alice Zhang"


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
    session_id = db.create_grading_session("Active grading", "rubric.json", "answer.json")
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


def test_student_delete_is_rejected_when_session_is_running_without_a_ledger(
    tmp_path,
) -> None:
    from db_manager import StudentRecord

    client, db = _client_with_db(tmp_path)
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session(
        "Fallback active grading",
        "rubric.json",
        "answer.json",
    )
    db.update_session_status(session_id, "running")
    impact = client.get(f"/api/students/{student_id}/deletion-impact").json()

    response = client.delete(
        f"/api/students/{student_id}",
        params={
            "expected_revision": impact["roster_revision"],
            "confirmed": "true",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "student_grading_active"
    assert db.list_students()[0]["student_code"] == "S001"
    assert list(db.backup_dir.glob("grading_before_delete_student_*.db")) == []


def test_student_delete_succeeds_after_a_paused_run_is_cancelled(tmp_path) -> None:
    from backend.scan_grading.workspace import ScanGradingWorkspace
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db = _client_with_db(tmp_path)
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session(
        "Cancelled grading",
        "rubric.json",
        "answer.json",
    )
    run_store = GradingRunStore(db.db_path)
    run = run_store.begin(session_id, "a" * 64, "full_paper")
    run_store.add_item(
        run.id,
        source_label="cancelled-001",
        student_id=student_id,
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    run_store.finish(run.run_token, "paused")
    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        grading_db_path=db.db_path,
    )
    cancelled = workspace.record_cancel_request(
        session_id,
        run.id,
        None,
        confirmed=True,
    )
    assert cancelled["state"] == "cancelled"
    impact = client.get(f"/api/students/{student_id}/deletion-impact").json()

    response = client.delete(
        f"/api/students/{student_id}",
        params={
            "expected_revision": impact["roster_revision"],
            "confirmed": "true",
        },
    )

    assert response.status_code == 200
    assert db.list_students() == []
    assert run_store.counts(run.id)["pending"] == 0


def test_student_delete_reports_backup_failure_without_exposing_details(
    tmp_path,
    monkeypatch,
) -> None:
    from db_manager import StudentRecord

    client, db = _client_with_db(tmp_path)
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = db.list_students()[0]["id"]
    impact = client.get(f"/api/students/{student_id}/deletion-impact").json()

    def fail_backup(*_args, **_kwargs):
        raise OSError("private backup path is unavailable")

    monkeypatch.setattr(db, "create_backup", fail_backup)
    response = client.delete(
        f"/api/students/{student_id}",
        params={
            "expected_revision": impact["roster_revision"],
            "confirmed": "true",
        },
        headers={"x-request-id": "rid-delete-backup"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "student_backup_failed",
            "message": "Student backup failed; no student data was deleted",
            "details": {},
            "request_id": "rid-delete-backup",
        }
    }
    assert db.list_students()[0]["student_code"] == "S001"
