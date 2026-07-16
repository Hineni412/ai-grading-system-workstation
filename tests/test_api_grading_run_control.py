from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient


def _system(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_exams_dir,
        get_grading_db,
        get_job_manager,
        get_templates_dir,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_exams_dir] = lambda: tmp_path / "exams"
    app.dependency_overrides[get_templates_dir] = lambda: tmp_path / "templates"
    return TestClient(app), db, manager


def test_workspace_projects_run_counts_and_requests_safe_pause(tmp_path) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(tmp_path)
    db.upsert_students(
        [StudentRecord("S001", "学生甲", "七年级 1 班"), StudentRecord("S002", "学生乙", "七年级 1 班")]
    )
    student_ids = [int(student["id"]) for student in db.list_students()]
    session_id = db.create_grading_session("匿名月考", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "full_paper")
    first = store.add_item(
        run.id,
        source_label="001",
        student_id=student_ids[0],
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    store.add_item(
        run.id,
        source_label="002",
        student_id=student_ids[1],
        paper_fingerprint="c" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    store.set_item_status(first, "graded")
    active_job = manager.store.create_job("grading_run", {"session_id": session_id})
    try:
        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        assert loaded.json()["grading_run"] == {
            "run_id": run.id,
            "job_id": active_job.id,
            "job_status": "queued",
            "progress": 0.0,
            "started_at": None,
            "updated_at": active_job.updated_at,
            "mode": "full_paper",
            "state": "running",
            "counts": {
                "graded": 1,
                "grading": 0,
                "pending": 1,
                "skipped": 0,
                "failed": 0,
                "conflict": 0,
                "total": 2,
            },
            "allowed_actions": ["pause", "cancel"],
        }

        paused = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/pause"
        )
        assert paused.status_code == 200
        assert paused.json()["state"] == "pause_requested"
        assert paused.json()["allowed_actions"] == ["cancel"]
    finally:
        manager.shutdown()


def test_cancelled_run_cannot_resume_and_failed_retry_keeps_original_mode(tmp_path) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(tmp_path)
    db.upsert_students([StudentRecord("S001", "学生甲", "七年级 1 班")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session("匿名周测", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    cancelled_run = store.begin(session_id, "a" * 64, "hybrid_batch")
    manager.register("grading_run", lambda context: {"state": "completed"})
    queued = manager.store.create_job("grading_run", {"session_id": session_id})
    try:
        cancelled = client.post(
            f"/api/sessions/{session_id}/grading/runs/{cancelled_run.id}/cancel",
            json={"job_id": queued.id},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["allowed_actions"] == []

        resume = client.post(
            f"/api/sessions/{session_id}/grading/runs/{cancelled_run.id}/resume"
        )
        assert resume.status_code == 409

        failed_run = store.begin(session_id, "b" * 64, "full_paper")
        store.add_item(
            failed_run.id,
            source_label="failed-001",
            student_id=student_id,
            paper_fingerprint="c" * 64,
            config_fingerprint="b" * 64,
            status="failed",
        )
        store.finish(failed_run.run_token, "failed")
        retryable = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert retryable.json()["grading_run"]["allowed_actions"] == ["retry_failed"]
        retried = client.post(
            f"/api/sessions/{session_id}/grading/runs/{failed_run.id}/retry-failed"
        )
        assert retried.status_code == 202
        assert retried.json()["payload"]["grading_mode"] == "full_paper"
        assert retried.json()["payload"]["failed_only"] is True
        assert retried.json()["payload"]["source_run_id"] == failed_run.id
    finally:
        manager.shutdown()


def test_start_requires_current_frozen_preflight_and_pending_issue_confirmation(tmp_path) -> None:
    client, db, manager = _system(tmp_path)
    session_id = db.create_grading_session("匿名期末", "rubric.json", "answer.json")
    manager.register("grading_run", lambda context: {"state": "completed"})
    content = b"front"
    try:
        client.post(
            f"/api/sessions/{session_id}/scan-uploads",
            content=content,
            headers={
                "content-type": "image/jpeg",
                "x-upload-filename": "front.jpg",
                "x-content-sha256": hashlib.sha256(content).hexdigest(),
            },
        )
        client.post(
            f"/api/sessions/{session_id}/scan-uploads/freeze",
            json={"expected_revision": 1},
        )
        scan_file = next((tmp_path / "exams").rglob("*.jpg"))
        analysis_path = tmp_path / "templates" / f"session_{session_id}" / "scan_analysis_latest.json"
        analysis_path.write_text(
            json.dumps(
                {
                    "groups": [],
                    "issues": [
                        {
                            "issue_id": "issue-1",
                            "issue_type": "missing_back",
                            "message": "missing back",
                            "front_image": str(scan_file),
                            "back_image": None,
                            "source_label": "001",
                        }
                    ],
                    "absent_students": [],
                    "warnings": [],
                    "total_pages": 1,
                }
            ),
            encoding="utf-8",
        )

        rejected_path = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={"exams_dir": "C:/private", "grading_mode": "full_paper"},
        )
        assert rejected_path.status_code == 422

        unconfirmed = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "full_paper",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": False,
            },
        )
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["error"]["code"] == "pending_scan_issues_not_confirmed"

        started = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "hybrid_batch",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": True,
            },
        )
        assert started.status_code == 202
        assert started.json()["payload"]["grading_mode"] == "hybrid_batch"
        assert "exams_dir" not in started.json()["payload"]
    finally:
        manager.shutdown()
