from __future__ import annotations

import hashlib
import json
from urllib.parse import quote

from fastapi.testclient import TestClient


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

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_exams_dir] = lambda: tmp_path / "exams"
    app.dependency_overrides[get_templates_dir] = lambda: tmp_path / "templates"
    return TestClient(app), db, manager


def test_scan_upload_routes_publish_safe_queue_and_freeze_it(tmp_path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("匿名月考", "rubric.json", "answer.json")
    content = b"anonymous-jpeg"
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


def test_scan_upload_routes_remove_and_clear_draft_queue(tmp_path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = db.create_grading_session("匿名周测", "rubric.json", "answer.json")
    try:
        ids = []
        for index, content in enumerate((b"front", b"back"), start=1):
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
        for index, content in enumerate((b"front", b"back"), start=1):
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
        group_id = loaded.json()["groups"][0]["id"]
        media = client.get(loaded.json()["groups"][0]["front_media_url"])
        assert media.status_code == 200
        assert media.content in {b"front", b"back"}
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
                        "action": "invalid",
                    },
                ],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["pending_issue_count"] == 0

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

    def handler(context):
        return {
            "session_id": context.payload["session_id"],
            "summary": {"auto_matched": 1, "issues": 0, "absent_candidates": 0, "total_pages": 2},
            "scan_analysis_path": str(tmp_path / "private" / "scan.json"),
        }

    manager.register("scan_analysis", handler)
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
