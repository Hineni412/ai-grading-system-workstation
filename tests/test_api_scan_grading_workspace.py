from __future__ import annotations

import hashlib
import io
import json
import threading
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


def _configure_scan_prerequisites(db, tmp_path, session_id: int) -> None:
    from PIL import Image

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
    db.upsert_session_template(
        session_id,
        str(front_path),
        str(back_path),
    )
    db.mark_template_confirmed(session_id, True)


def _jpeg(payload: bytes) -> bytes:
    return b"\xff\xd8\xff" + payload


def _png(payload: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + payload


def _client(tmp_path):
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
    return TestClient(app), db, manager


def test_scan_upload_routes_publish_safe_queue_and_freeze_it(tmp_path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("匿名月考", "rubric.json", "answer.json")
    content = _jpeg(b"anonymous-jpeg")
    digest = hashlib.sha256(content).hexdigest()
    headers = {
        "content-type": "image/jpeg",
        "x-upload-filename": quote("七年级-001.jpg"),
        "x-content-sha256": digest,
    }
    try:
        created = client.post(
            f"/api/sessions/{session_id}/scan-uploads",
            content=content,
            headers=headers,
        )
        duplicate = client.post(
            f"/api/sessions/{session_id}/scan-uploads",
            content=content,
            headers=headers,
        )

        assert created.status_code == 201
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        workspace = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert workspace.status_code == 200
        assert workspace.json()["upload_batch"]["file_count"] == 1
        assert str(tmp_path) not in workspace.text
        assert "storage_name" not in workspace.text

        frozen = client.post(
            f"/api/sessions/{session_id}/scan-uploads/freeze",
            json={"expected_revision": 1},
        )
        assert frozen.status_code == 200
        assert frozen.json()["state"] == "frozen"

        rejected = client.post(
            f"/api/sessions/{session_id}/scan-uploads",
            content=b"second",
            headers={
                **headers,
                "x-upload-filename": "002.jpg",
                "x-content-sha256": hashlib.sha256(b"second").hexdigest(),
            },
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "scan_upload_batch_frozen"
    finally:
        manager.shutdown()


@pytest.mark.parametrize(
    ("filename", "media_type", "content"),
    [
        ("forged.jpg", "image/jpeg", b"this is not a jpeg"),
        ("forged.png", "image/png", b"this is not a png"),
        ("forged.pdf", "application/pdf", b"this is not a pdf"),
        ("jpeg-as-png.png", "image/png", _jpeg(b"wrong declared type")),
    ],
)
def test_scan_upload_rejects_false_file_type_without_changing_the_batch(
    tmp_path,
    filename: str,
    media_type: str,
    content: bytes,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("格式校验", "rubric.json", "answer.json")
    try:
        rejected = client.post(
            f"/api/sessions/{session_id}/scan-uploads",
            content=content,
            headers={
                "content-type": media_type,
                "x-upload-filename": filename,
                "x-content-sha256": hashlib.sha256(content).hexdigest(),
            },
        )

        assert rejected.status_code == 415
        assert rejected.json()["error"]["code"] == "invalid_scan_upload"
        batch = client.get(f"/api/sessions/{session_id}/grading-workspace").json()[
            "upload_batch"
        ]
        assert batch["revision"] == 0
        assert batch["files"] == []
        assert not list((tmp_path / "exams").rglob("*.tmp"))
    finally:
        manager.shutdown()


def test_scan_upload_routes_remove_and_clear_draft_queue(tmp_path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("匿名周测", "rubric.json", "answer.json")
    try:
        ids = []
        for index, content in enumerate((_png(b"front"), _png(b"back")), start=1):
            response = client.post(
                f"/api/sessions/{session_id}/scan-uploads",
                content=content,
                headers={
                    "content-type": "image/png",
                    "x-upload-filename": f"{index}.png",
                    "x-content-sha256": hashlib.sha256(content).hexdigest(),
                },
            )
            ids.append(response.json()["file"]["id"])

        removed = client.delete(
            f"/api/sessions/{session_id}/scan-uploads/{ids[0]}",
            params={"expected_revision": 2},
        )
        cleared = client.delete(
            f"/api/sessions/{session_id}/scan-uploads",
            params={"expected_revision": 3},
        )

        assert removed.status_code == 200
        assert removed.json()["file_count"] == 1
        assert cleared.status_code == 200
        assert cleared.json()["file_count"] == 0
    finally:
        manager.shutdown()


def test_preflight_routes_return_safe_snapshot_and_revisioned_decisions(tmp_path) -> None:
    from db_manager import StudentRecord

    client, db, manager = _client(tmp_path)
    db.upsert_students(
        [
            StudentRecord("S001", "学生甲", "七年级 1 班"),
            StudentRecord("S002", "学生乙", "七年级 1 班"),
        ]
    )
    students = db.list_students()
    session_id = db.create_grading_session("匿名期中", "rubric.json", "answer.json")
    try:
        for index, content in enumerate((_jpeg(b"front"), _jpeg(b"back")), start=1):
            client.post(
                f"/api/sessions/{session_id}/scan-uploads",
                content=content,
                headers={
                    "content-type": "image/jpeg",
                    "x-upload-filename": f"{index}.jpg",
                    "x-content-sha256": hashlib.sha256(content).hexdigest(),
                },
            )
        frozen = client.post(
            f"/api/sessions/{session_id}/scan-uploads/freeze",
            json={"expected_revision": 2},
        )
        scan_files = sorted((tmp_path / "exams").rglob("*.jpg"))
        analysis_path = tmp_path / "templates" / f"session_{session_id}" / "scan_analysis_latest.json"
        analysis_path.write_text(
            json.dumps(
                {
                    "scan_batch_id": frozen.json()["batch_id"],
                    "groups": [
                        {
                            "front_image": str(scan_files[0]),
                            "back_image": str(scan_files[1]),
                            "source_label": "001",
                            "student_id": students[0]["id"],
                            "student_name": students[0]["name"],
                            "detected_name": "学生中",
                            "match_method": "fuzzy",
                            "match_score": 0.7,
                        }
                    ],
                    "issues": [
                        {
                            "issue_id": "issue-1",
                            "issue_type": "unmatched",
                            "message": "private path C:/student.jpg",
                            "front_image": str(scan_files[0]),
                            "back_image": str(scan_files[1]),
                            "source_label": "002",
                        }
                    ],
                    "absent_students": [],
                    "warnings": [],
                    "total_pages": 4,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        loaded = client.get(f"/api/sessions/{session_id}/scan/preflight")
        assert loaded.status_code == 200
        assert str(tmp_path) not in loaded.text
        assert loaded.json()["summary"]["ready_to_grade"] == 1
        assert loaded.json()["pending_issue_count"] == 1  # Only the unmatched issue remains.
        assert loaded.json()["match_conflicts"] == []
        assert client.get(f"/api/sessions/{session_id}/scan/preflight").json()["summary"]["ready_to_grade"] == 1
        group_id = loaded.json()["groups"][0]["id"]
        media = client.get(loaded.json()["groups"][0]["front_media_url"])
        assert media.status_code == 200
        assert media.content in {_jpeg(b"front"), _jpeg(b"back")}
        assert client.get(
            f"/api/sessions/{session_id}/scan/preflight/media/issue:forged:front"
        ).status_code == 404

        saved = client.put(
            f"/api/sessions/{session_id}/scan/preflight/decisions",
            json={
                "expected_revision": 0,
                "decisions": [
                    {
                        "target_type": "group",
                        "target_id": group_id,
                        "action": "match",
                        "student_id": students[1]["id"],
                    },
                    {
                        "target_type": "issue",
                        "target_id": "issue-1",
                        "action": "match",
                        "student_id": students[0]["id"],
                    },
                ],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["pending_issue_count"] == 0
        assert saved.json()["ready_to_grade"] == 2
        refreshed = client.get(f"/api/sessions/{session_id}/scan/preflight")
        assert refreshed.json()["summary"]["ready_to_grade"] == 2

        duplicate_decisions = [dict(item) for item in saved.json()["decisions"]]
        duplicate_decisions[1]["student_id"] = duplicate_decisions[0]["student_id"]
        rejected = client.put(
            f"/api/sessions/{session_id}/scan/preflight/decisions",
            json={"expected_revision": saved.json()["revision"], "decisions": duplicate_decisions},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "scan_match_conflict"
        assert len(rejected.json()["error"]["details"]["conflicts"][0]["targets"]) == 2
        unchanged = client.get(f"/api/sessions/{session_id}/scan/preflight").json()
        assert unchanged["revision"] == saved.json()["revision"]
        assert unchanged["decisions"] == saved.json()["decisions"]
        partial = client.put(
            f"/api/sessions/{session_id}/scan/preflight/decisions",
            json={"expected_revision": saved.json()["revision"], "decisions": duplicate_decisions, "allow_partial_matches": True},
        )
        assert partial.status_code == 200
        assert partial.json()["decisions"] == saved.json()["decisions"]
        assert partial.json()["revision"] == saved.json()["revision"]
        assert len(partial.json()["rejected_conflicts"][0]["targets"]) == 2

        stale = client.put(
            f"/api/sessions/{session_id}/scan/preflight/decisions",
            json={"expected_revision": 0, "decisions": []},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "scan_decision_revision_conflict"
    finally:
        manager.shutdown()


def test_scan_analysis_uses_frozen_server_batch_and_rejects_client_paths(tmp_path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("匿名模拟考", "rubric.json", "answer.json")
    _configure_scan_prerequisites(db, tmp_path, session_id)

    def handler(context):
        return {
            "session_id": context.payload["session_id"],
            "summary": {"auto_matched": 1, "issues": 0, "absent_candidates": 0, "total_pages": 2},
            "scan_analysis_path": str(tmp_path / "private" / "scan.json"),
        }

    manager.register("scan_analysis", handler)
    content = _jpeg(b"front")
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

        rejected = client.post(
            f"/api/sessions/{session_id}/scan/analyze",
            json={"exams_dir": "C:/private/scans"},
        )
        assert rejected.status_code == 422

        created = client.post(f"/api/sessions/{session_id}/scan/analyze", json={})
        assert created.status_code == 202
        assert "exams_dir" not in created.json()["payload"]
        internal = manager.get(created.json()["id"])
        assert internal is not None
        assert str(internal.payload["exams_dir"]).startswith(str(tmp_path / "exams"))
        assert internal.payload["scan_batch_id"] == frozen.json()["batch_id"]
    finally:
        manager.shutdown()


def test_active_preflight_is_server_projected_and_blocks_new_batch(tmp_path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("预检批次隔离", "rubric.json", "answer.json")
    _configure_scan_prerequisites(db, tmp_path, session_id)
    release = threading.Event()

    def blocking_handler(_context):
        release.wait(timeout=5)
        return {"state": "completed"}

    manager.register("scan_analysis", blocking_handler)
    content = _jpeg(b"front")
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
        ).json()
        created = client.post(f"/api/sessions/{session_id}/scan/analyze", json={})
        assert created.status_code == 202

        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        projected = loaded.json()["scan_analysis_job"]
        assert projected["id"] == created.json()["id"]
        assert projected["status"] in {"queued", "running"}
        assert projected["scan_batch_id"] == frozen["batch_id"]

        rejected = client.post(f"/api/sessions/{session_id}/scan-uploads/new-batch")
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "scan_analysis_still_active"
    finally:
        release.set()
        if 'created' in locals() and created.status_code == 202:
            manager.wait(created.json()["id"], timeout=5)
        manager.shutdown()


def test_preflight_submission_and_new_batch_share_one_session_lock(
    tmp_path,
    monkeypatch,
) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.scan_grading.workspace import (
        ActiveScanAnalysisError,
        ScanGradingWorkspace,
    )

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    job_release = threading.Event()

    def blocking_handler(_context):
        job_release.wait(timeout=5)
        return {"state": "completed"}

    manager.register("scan_analysis", blocking_handler)
    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        job_manager=manager,
    )
    content = _jpeg(b"front")
    workspace.add_upload(
        1,
        filename="front.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    workspace.freeze_uploads(1, expected_revision=1)

    submit_entered = threading.Event()
    submit_release = threading.Event()
    original_submit = manager.submit_unique_active

    def delayed_submit(job_type, payload=None):
        submit_entered.set()
        assert submit_release.wait(timeout=5)
        return original_submit(job_type, payload)

    monkeypatch.setattr(manager, "submit_unique_active", delayed_submit)
    submitted: dict[str, object] = {}
    batch_result: dict[str, object] = {}

    def submit_preflight() -> None:
        submitted["job"] = workspace.submit_scan_analysis(1, {"enhance_images": True})

    def start_batch() -> None:
        try:
            batch_result["batch"] = workspace.start_new_upload_batch(1)
        except Exception as exc:  # noqa: BLE001 - the assertion checks the public error type
            batch_result["error"] = exc

    preflight_thread = threading.Thread(target=submit_preflight)
    batch_thread = threading.Thread(target=start_batch)
    try:
        preflight_thread.start()
        assert submit_entered.wait(timeout=5)
        batch_thread.start()
        batch_thread.join(timeout=0.1)
        assert batch_thread.is_alive()

        submit_release.set()
        preflight_thread.join(timeout=5)
        batch_thread.join(timeout=5)

        assert "job" in submitted
        assert isinstance(batch_result.get("error"), ActiveScanAnalysisError)
    finally:
        submit_release.set()
        job_release.set()
        preflight_thread.join(timeout=5)
        batch_thread.join(timeout=5)
        job = submitted.get("job")
        if job is not None:
            manager.wait(job.id, timeout=5)
        manager.shutdown()


def test_scan_replacement_cleanup_pending_is_reported_as_partial_success(
    tmp_path,
) -> None:
    from backend.api.dependencies import get_scan_grading_workspace
    from backend.scan_grading.workspace import (
        ScanReplacementCleanupIncompleteError,
    )

    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session(
        "答卷替换清理",
        "rubric.json",
        "answer.json",
    )

    class CleanupPendingWorkspace:
        def commit_replacement_upload(
            self,
            _session_id: int,
            *,
            expected_revision: int,
        ) -> dict[str, object]:
            del expected_revision
            raise ScanReplacementCleanupIncompleteError(
                "replacement cleanup is incomplete"
            )

    client.app.dependency_overrides[get_scan_grading_workspace] = (
        CleanupPendingWorkspace
    )
    try:
        response = client.post(
            f"/api/sessions/{session_id}/scan-uploads/replacement/commit",
            json={"expected_revision": 1},
        )

        assert response.status_code == 409
        assert (
            response.json()["error"]["code"]
            == "scan_replacement_cleanup_pending"
        )
        assert "已经替换" in response.json()["error"]["message"]
        assert "旧文件" in response.json()["error"]["message"]
    finally:
        manager.shutdown()


def test_failed_preflight_after_restart_is_projected_and_can_be_retried(tmp_path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("预检重启恢复", "rubric.json", "answer.json")
    _configure_scan_prerequisites(db, tmp_path, session_id)
    content = _jpeg(b"front")
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
        ).json()
        interrupted = manager.store.create_job(
            "scan_analysis",
            {"session_id": session_id, "scan_batch_id": frozen["batch_id"]},
        )
        assert manager.store.mark_running(interrupted.id) is True
        manager.store.request_cancel(interrupted.id)
        manager.store.fail_interrupted_jobs()
        manager.register("scan_analysis", lambda _context: {"state": "completed"})

        loaded = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert loaded.status_code == 200
        assert loaded.json()["scan_analysis_job"]["id"] == interrupted.id
        assert loaded.json()["scan_analysis_job"]["status"] == "failed"

        retried = client.post(f"/api/sessions/{session_id}/scan/analyze", json={})
        assert retried.status_code == 202
        assert retried.json()["id"] != interrupted.id
    finally:
        manager.shutdown()
