from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _configure_preflight_binding(db, tmp_path, session_id: int) -> dict:
    from PIL import Image

    from backend.config_workspace.publish import load_editor_config
    from backend.repositories.compat import open_grading_repositories
    from template_upload_service import TemplateUploadService

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    rubric_path = config_dir / f"rubric-{session_id}.json"
    answer_path = config_dir / f"answer-{session_id}.json"
    rubric_path.write_text(
        json.dumps({"total_score": 0, "questions": []}),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps({"questions": []}),
        encoding="utf-8",
    )
    db.update_grading_session_config(
        session_id,
        rubric_path=str(rubric_path),
        answer_key_path=str(answer_path),
    )
    session_dir = tmp_path / "templates" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    front_path = session_dir / "front.png"
    back_path = session_dir / "back.png"
    Image.new("RGB", (16, 16), "white").save(front_path)
    Image.new("RGB", (16, 16), "black").save(back_path)
    template_id = db.upsert_session_template(
        session_id,
        str(front_path),
        str(back_path),
    )
    db.mark_template_confirmed(session_id, True)
    current = TemplateUploadService(tmp_path / "templates").load_current(
        db=open_grading_repositories(db.db_path),
        session_id=session_id,
    )
    return {
        "config_revision": load_editor_config(db, session_id).revision,
        "template_id": template_id,
        "template_fingerprint": current.template_fingerprint,
        "template_first_page_role": current.first_page_role,
        "expected_rubric_path": str(rubric_path),
        "expected_answer_key_path": str(answer_path),
        "expected_template_id": template_id,
        "expected_front_template_path": str(front_path),
        "expected_back_template_path": str(back_path),
    }


def _system(tmp_path, *, config_fingerprint_resolver=None):
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

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(db.db_path), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_exams_dir] = lambda: tmp_path / "exams"
    app.dependency_overrides[get_templates_dir] = lambda: tmp_path / "templates"
    if config_fingerprint_resolver is not None:
        from backend.api.dependencies import get_scan_grading_workspace
        from backend.scan_grading.workspace import ScanGradingWorkspace

        grading_workspace = ScanGradingWorkspace(
            exams_root=tmp_path / "exams",
            templates_root=tmp_path / "templates",
            grading_db_path=db.db_path,
            job_manager=manager,
            config_fingerprint_resolver=config_fingerprint_resolver,
        )
        app.dependency_overrides[get_scan_grading_workspace] = lambda: grading_workspace
    return TestClient(app), db, manager


def _prepare_ready_scan_batch(client, db, tmp_path, session_id: int) -> dict:
    from db_manager import StudentRecord

    if not db.list_students():
        db.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student = db.list_students()[0]
    binding = _configure_preflight_binding(db, tmp_path, session_id)
    content = b"\xff\xd8\xffready scan"
    uploaded = client.post(
        f"/api/sessions/{session_id}/scan-uploads",
        content=content,
        headers={
            "content-type": "image/jpeg",
            "x-upload-filename": "ready.jpg",
            "x-content-sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    assert uploaded.status_code == 201
    frozen = client.post(
        f"/api/sessions/{session_id}/scan-uploads/freeze",
        json={"expected_revision": 1},
    )
    assert frozen.status_code == 200
    scan_file = next((tmp_path / "exams").rglob("*.jpg"))
    analysis_path = (
        tmp_path / "templates" / f"session_{session_id}" / "scan_analysis_latest.json"
    )
    analysis_path.write_text(
        json.dumps(
            {
                "scan_batch_id": frozen.json()["batch_id"],
                "groups": [
                    {
                        "source_label": "001",
                        "front_image": str(scan_file),
                        "back_image": None,
                        "student_id": int(student["id"]),
                        "student_name": student["name"],
                        "match_method": "exact",
                        "match_score": 1.0,
                    }
                ],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 1,
                "config_revision": binding["config_revision"],
                "template_id": binding["template_id"],
                "template_fingerprint": binding["template_fingerprint"],
                "template_first_page_role": binding["template_first_page_role"],
            }
        ),
        encoding="utf-8",
    )
    result = frozen.json()
    result["_binding"] = binding
    return result


def test_cancelled_run_cannot_resume_and_failed_retry_keeps_original_mode(
    tmp_path,
) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(tmp_path)
    db.upsert_students([StudentRecord("S001", "学生甲", "七年级 1 班")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session("匿名周测", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    cancelled_run = store.begin(session_id, "a" * 64, "hybrid_batch")
    manager.register("grading_run", lambda context: {"state": "completed"})
    wrong_job = manager.store.create_job("grading_run", {"session_id": session_id})
    queued = manager.store.create_job("grading_run", {"session_id": session_id})
    try:
        mismatched = client.post(
            f"/api/sessions/{session_id}/grading/runs/{cancelled_run.id}/cancel",
            json={"job_id": wrong_job.id},
        )
        assert mismatched.status_code == 409
        assert mismatched.json()["error"]["code"] == "grading_job_run_mismatch"
        manager.cancel(wrong_job.id)

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
        supplement = client.post(
            f"/api/sessions/{session_id}/grading/runs/{cancelled_run.id}/supplement-new-matches"
        )
        assert supplement.status_code == 409

        new_batch = client.post(f"/api/sessions/{session_id}/scan-uploads/new-batch")
        assert new_batch.status_code == 200
        assert new_batch.json()["state"] == "draft"
        refreshed = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert refreshed.status_code == 200
        assert refreshed.json()["grading_run"] is None

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
        assert retryable.json()["grading_run"]["allowed_actions"] == [
            "retry_failed",
            "supplement_new_matches",
        ]
        retried = client.post(
            f"/api/sessions/{session_id}/grading/runs/{failed_run.id}/retry-failed"
        )
        assert retried.status_code == 202
        assert retried.json()["payload"]["grading_mode"] == "full_paper"
        assert retried.json()["payload"]["failed_only"] is True
        assert retried.json()["payload"]["source_run_id"] == failed_run.id
        assert "max_workers" not in retried.json()["payload"]
    finally:
        manager.shutdown()


def test_concurrent_start_requests_create_only_one_grading_job(tmp_path) -> None:
    from db_manager import StudentRecord

    client, db, manager = _system(tmp_path)
    db.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    session_id = db.create_grading_session("并发启动测试", "rubric.json", "answer.json")
    binding = _configure_preflight_binding(db, tmp_path, session_id)
    content = b"\xff\xd8\xfffront"
    client.post(
        f"/api/sessions/{session_id}/scan-uploads",
        content=content,
        headers={
            "content-type": "image/jpeg",
            "x-upload-filename": "front.jpg",
            "x-content-sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    frozen = client.post(
        f"/api/sessions/{session_id}/scan-uploads/freeze",
        json={"expected_revision": 1},
    )
    scan_file = next((tmp_path / "exams").rglob("*.jpg"))
    analysis_path = (
        tmp_path / "templates" / f"session_{session_id}" / "scan_analysis_latest.json"
    )
    analysis_path.write_text(
        json.dumps(
            {
                "scan_batch_id": frozen.json()["batch_id"],
                "groups": [
                    {
                        "source_label": "001",
                        "front_image": str(scan_file),
                        "back_image": None,
                        "student_id": 1,
                        "match_method": "exact",
                        "match_score": 1.0,
                    }
                ],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 1,
                "config_revision": binding["config_revision"],
                "template_id": binding["template_id"],
                "template_fingerprint": binding["template_fingerprint"],
                "template_first_page_role": binding["template_first_page_role"],
            }
        ),
        encoding="utf-8",
    )
    release_handler = threading.Event()

    def wait_for_release(_context):
        release_handler.wait(timeout=5)
        return {"state": "completed"}

    manager.register("grading_run", wait_for_release)
    request = {
        "grading_mode": "full_paper",
        "upload_revision": 2,
        "decision_revision": 0,
        "confirm_pending_issues": False,
    }
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda _index: client.post(
                        f"/api/sessions/{session_id}/grading/run",
                        json=request,
                    ),
                    range(2),
                )
            )
        assert sorted(response.status_code for response in responses) == [202, 409]
        jobs, total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert total == 1
        assert len(jobs) == 1
        assert "max_workers" not in jobs[0].payload
        projected = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert projected.status_code == 200
        assert projected.json()["grading_run"] is None
        grading_job = projected.json()["grading_job"]
        assert grading_job["id"] == jobs[0].id
        assert grading_job["status"] in {"queued", "running"}
        assert grading_job["scan_batch_id"] == frozen.json()["batch_id"]
        assert grading_job["cancel_requested"] is False
        release_handler.set()
        manager.wait(jobs[0].id, timeout=2)
        completed = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert completed.status_code == 200
        assert completed.json()["grading_run"] is None
        assert completed.json()["grading_job"]["status"] == "succeeded"
        repeated = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json=request,
        )
        assert repeated.status_code == 409
        _jobs, repeated_total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert repeated_total == 1
    finally:
        release_handler.set()
        manager.shutdown()


def test_start_requires_current_frozen_preflight_and_pending_issue_confirmation(
    tmp_path,
) -> None:
    client, db, manager = _system(tmp_path)
    session_id = db.create_grading_session("匿名期末", "rubric.json", "answer.json")
    binding = _configure_preflight_binding(db, tmp_path, session_id)
    manager.register("grading_run", lambda context: {"state": "completed"})
    content = b"\xff\xd8\xfffront"
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
        frozen = client.post(
            f"/api/sessions/{session_id}/scan-uploads/freeze",
            json={"expected_revision": 1},
        )
        scan_file = next((tmp_path / "exams").rglob("*.jpg"))
        analysis_path = (
            tmp_path
            / "templates"
            / f"session_{session_id}"
            / "scan_analysis_latest.json"
        )
        analysis_path.write_text(
            json.dumps(
                {
                    "scan_batch_id": frozen.json()["batch_id"],
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
                    "config_revision": binding["config_revision"],
                    "template_id": binding["template_id"],
                    "template_fingerprint": binding["template_fingerprint"],
                    "template_first_page_role": binding["template_first_page_role"],
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
        assert (
            unconfirmed.json()["error"]["code"] == "pending_scan_issues_not_confirmed"
        )

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

        from grading_run_store import GradingRunStore

        active_run = GradingRunStore(db.db_path).begin(
            session_id, "d" * 64, "hybrid_batch"
        )
        duplicate = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "hybrid_batch",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": True,
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "grading_input_not_ready"
        GradingRunStore(db.db_path).finish(active_run.run_token, "completed")
    finally:
        manager.shutdown()


def test_resume_rejects_changed_grading_configuration_before_submitting_job(
    tmp_path,
) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(
        tmp_path,
        config_fingerprint_resolver=lambda _session_id, _mode: "b" * 64,
    )
    db.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session("配置变化恢复", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "full_paper")
    store.add_item(
        run.id,
        source_label="001",
        student_id=student_id,
        paper_fingerprint="c" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    store.finish(run.run_token, "paused")
    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        response = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/resume"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "grading_config_changed"
        jobs, total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert total == 0
        assert jobs == []
        assert store.latest(session_id).id == run.id
    finally:
        manager.shutdown()


@pytest.mark.parametrize("grading_mode", ["hybrid_batch", "full_paper"])
def test_terminal_run_can_submit_separate_original_mode_supplement(
    tmp_path,
    grading_mode: str,
) -> None:
    from grading_run_store import GradingRunStore

    client, db, manager = _system(
        tmp_path,
        config_fingerprint_resolver=lambda _session_id, _mode: "a" * 64,
    )
    session_id = db.create_grading_session("异常卷补批", "rubric.json", "answer.json")
    frozen = _prepare_ready_scan_batch(client, db, tmp_path, session_id)
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, grading_mode)
    store.finish(run.run_token, "completed")
    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        assert (
            "supplement_new_matches" in loaded.json()["grading_run"]["allowed_actions"]
        )

        submitted = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/supplement-new-matches"
        )
        assert submitted.status_code == 202
        expected_payload = {
            "session_id": session_id,
            "grading_mode": grading_mode,
            "failed_only": False,
            "supplement_only": True,
            "supplement_run_id": run.id,
            "enhance_images": True,
            "scan_batch_id": frozen["batch_id"],
        }
        assert submitted.json()["payload"] == expected_payload
    finally:
        manager.shutdown()
