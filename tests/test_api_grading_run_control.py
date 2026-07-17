from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


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

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
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


def _prepare_ready_scan_batch(client, tmp_path, session_id: int) -> dict:
    content = b"ready scan"
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
        tmp_path
        / "templates"
        / f"session_{session_id}"
        / "scan_analysis_latest.json"
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
                    }
                ],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 1,
            }
        ),
        encoding="utf-8",
    )
    return frozen.json()


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
    finally:
        manager.shutdown()


def test_concurrent_start_requests_create_only_one_grading_job(tmp_path) -> None:
    client, db, manager = _system(tmp_path)
    session_id = db.create_grading_session("并发启动测试", "rubric.json", "answer.json")
    content = b"front"
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
                "groups": [
                    {
                        "source_label": "001",
                        "front_image": str(scan_file),
                        "back_image": None,
                        "student_id": 1,
                    }
                ],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 1,
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
        release_handler.set()
        manager.wait(jobs[0].id, timeout=2)
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


def test_new_batch_rejects_retry_from_the_previous_batch(tmp_path) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(tmp_path)
    db.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session("旧批次重试", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    failed_run = store.begin(session_id, "a" * 64, "full_paper")
    store.add_item(
        failed_run.id,
        source_label="001",
        student_id=student_id,
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="failed",
    )
    store.finish(failed_run.run_token, "failed")
    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        new_batch = client.post(f"/api/sessions/{session_id}/scan-uploads/new-batch")
        assert new_batch.status_code == 200

        stale_retry = client.post(
            f"/api/sessions/{session_id}/grading/runs/{failed_run.id}/retry-failed"
        )
        assert stale_retry.status_code == 409
        jobs, total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert total == 0
        assert jobs == []
    finally:
        manager.shutdown()


def test_restart_can_submit_when_manifest_has_orphaned_reservation(tmp_path) -> None:
    client, db, manager = _system(tmp_path)
    session_id = db.create_grading_session("启动中断恢复", "rubric.json", "answer.json")
    content = b"front"
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
    batch_id = frozen.json()["batch_id"]
    scan_file = next((tmp_path / "exams").rglob("*.jpg"))
    session_dir = tmp_path / "templates" / f"session_{session_id}"
    (session_dir / "scan_analysis_latest.json").write_text(
        json.dumps(
            {
                "scan_batch_id": batch_id,
                "groups": [
                    {
                        "source_label": "001",
                        "front_image": str(scan_file),
                        "back_image": None,
                        "student_id": 1,
                    }
                ],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 1,
            }
        ),
        encoding="utf-8",
    )
    manifest_path = session_dir / "scan_upload_batch.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["grading_submissions"] = {
        f"start:{batch_id}": {
            "token": "f" * 32,
            "reserved_at": "2026-07-17T00:00:00+00:00",
        }
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    interrupted_job, created = manager.store.create_idempotent_scan_grading_start(
        {
            "session_id": session_id,
            "scan_batch_id": batch_id,
            "grading_mode": "full_paper",
            "failed_only": False,
            "enhance_images": True,
            "exams_dir": str(scan_file.parent),
        }
    )
    assert created is True
    manager.store.fail_interrupted_jobs()
    assert manager.get(interrupted_job.id).status == "failed"
    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        recovered = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "full_paper",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": False,
            },
        )
        assert recovered.status_code == 202
        jobs, total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert total == 2
        assert len(jobs) == 2
        interrupted_status = next(
            job.status for job in jobs if job.id == interrupted_job.id
        )
        assert interrupted_status == "failed"
        recovered_job = next(job for job in jobs if job.id != interrupted_job.id)
        assert recovered_job.status in {"queued", "running", "succeeded"}
    finally:
        manager.shutdown()


@pytest.mark.parametrize("terminal_state", ["completed", "failed", "cancelled"])
def test_terminal_run_blocks_fresh_start_for_the_same_scan_batch(
    tmp_path,
    terminal_state: str,
) -> None:
    from grading_run_store import GradingRunStore

    client, db, manager = _system(tmp_path)
    session_id = db.create_grading_session(
        f"terminal-{terminal_state}",
        "rubric.json",
        "answer.json",
    )
    frozen = _prepare_ready_scan_batch(client, tmp_path, session_id)
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "full_paper")
    prior_job, created = manager.store.create_idempotent_scan_grading_start(
        {
            "session_id": session_id,
            "scan_batch_id": frozen["batch_id"],
            "grading_mode": "full_paper",
            "failed_only": False,
            "enhance_images": True,
            "exams_dir": str(next((tmp_path / "exams").rglob("*.jpg")).parent),
        }
    )
    assert created is True
    if terminal_state == "cancelled":
        cancelled = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/cancel",
            json={"job_id": prior_job.id},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
    else:
        assert manager.store.mark_running(prior_job.id) is True
        store.finish(run.run_token, terminal_state)
        manager.store.fail_interrupted_jobs()
        assert manager.get(prior_job.id).status == "failed"

    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        repeated = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "full_paper",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": False,
            },
        )

        assert repeated.status_code == 409
        assert repeated.json()["error"]["code"] == "grading_input_not_ready"
        jobs, total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert total == 1
        assert [job.id for job in jobs] == [prior_job.id]
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
        frozen = client.post(
            f"/api/sessions/{session_id}/scan-uploads/freeze",
            json={"expected_revision": 1},
        )
        scan_file = next((tmp_path / "exams").rglob("*.jpg"))
        analysis_path = tmp_path / "templates" / f"session_{session_id}" / "scan_analysis_latest.json"
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

        from grading_run_store import GradingRunStore

        active_run = GradingRunStore(db.db_path).begin(session_id, "d" * 64, "hybrid_batch")
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


def test_orphaned_running_ledger_projects_interrupted_and_can_resume(tmp_path) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(
        tmp_path,
        config_fingerprint_resolver=lambda _session_id, _mode: "a" * 64,
    )
    db.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session("重启恢复", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "full_paper")
    store.add_item(
        run.id, source_label="001", student_id=student_id,
        paper_fingerprint="b" * 64, config_fingerprint="a" * 64, status="pending",
    )
    manager.register("grading_run", lambda context: {"state": "completed"})
    try:
        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        assert loaded.json()["grading_run"]["state"] == "interrupted"
        assert loaded.json()["grading_run"]["allowed_actions"] == ["resume", "cancel"]

        resumed = client.post(f"/api/sessions/{session_id}/grading/runs/{run.id}/resume")
        assert resumed.status_code == 202
        assert resumed.json()["payload"]["resume_run_id"] == run.id
    finally:
        manager.shutdown()


def test_terminal_run_wins_an_unconfirmed_cancel_race(tmp_path) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(tmp_path)
    db.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session("取消完成竞态", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "full_paper")
    item_id = store.add_item(
        run.id,
        source_label="001",
        student_id=student_id,
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    store.set_item_status(item_id, "graded")
    store.finish(run.run_token, "completed")
    job = manager.store.create_job("grading_run", {"session_id": session_id})
    assert manager.store.mark_running(job.id) is True
    manager.store.finish(job.id, "succeeded", result={"state": "completed"})
    control_path = tmp_path / "templates" / f"session_{session_id}" / "grading_control_state.json"
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text(
        json.dumps(
            {
                "run_id": run.id,
                "job_id": job.id,
                "cancel_requested": True,
                "cancel_confirmed": False,
            }
        ),
        encoding="utf-8",
    )
    try:
        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        assert loaded.json()["grading_run"]["state"] == "completed"
        assert loaded.json()["grading_run"]["allowed_actions"] == [
            "supplement_new_matches"
        ]

        new_batch = client.post(f"/api/sessions/{session_id}/scan-uploads/new-batch")
        assert new_batch.status_code == 200
    finally:
        manager.shutdown()


def test_restart_does_not_leave_an_unconfirmed_cancel_request_stuck(tmp_path) -> None:
    from db_manager import StudentRecord
    from grading_run_store import GradingRunStore

    client, db, manager = _system(
        tmp_path,
        config_fingerprint_resolver=lambda _session_id, _mode: "a" * 64,
    )
    db.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student_id = int(db.list_students()[0]["id"])
    session_id = db.create_grading_session("取消后重启", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "full_paper")
    store.add_item(
        run.id,
        source_label="001",
        student_id=student_id,
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    job = manager.store.create_job("grading_run", {"session_id": session_id})
    assert manager.store.mark_running(job.id) is True
    manager.store.request_cancel(job.id)
    manager.store.fail_interrupted_jobs()
    control_path = tmp_path / "templates" / f"session_{session_id}" / "grading_control_state.json"
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text(
        json.dumps(
            {
                "run_id": run.id,
                "job_id": job.id,
                "cancel_requested": True,
                "cancel_confirmed": False,
            }
        ),
        encoding="utf-8",
    )
    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        assert loaded.json()["grading_run"]["state"] == "interrupted"
        assert loaded.json()["grading_run"]["allowed_actions"] == ["resume", "cancel"]

        resumed = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/resume"
        )
        assert resumed.status_code == 202
        assert not control_path.exists()
    finally:
        manager.shutdown()


def test_resume_rejects_changed_grading_configuration_before_submitting_job(tmp_path) -> None:
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


def test_terminal_run_can_submit_separate_original_mode_supplement(tmp_path) -> None:
    from grading_run_store import GradingRunStore

    client, db, manager = _system(
        tmp_path,
        config_fingerprint_resolver=lambda _session_id, _mode: "a" * 64,
    )
    session_id = db.create_grading_session("异常卷补批", "rubric.json", "answer.json")
    frozen = _prepare_ready_scan_batch(client, tmp_path, session_id)
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "hybrid_batch")
    store.finish(run.run_token, "completed")
    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        assert "supplement_new_matches" in loaded.json()["grading_run"]["allowed_actions"]

        submitted = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/supplement-new-matches"
        )
        assert submitted.status_code == 202
        assert submitted.json()["payload"] == {
            "session_id": session_id,
            "grading_mode": "hybrid_batch",
            "failed_only": False,
            "supplement_only": True,
            "supplement_run_id": run.id,
            "enhance_images": True,
            "scan_batch_id": frozen["batch_id"],
        }
    finally:
        manager.shutdown()
