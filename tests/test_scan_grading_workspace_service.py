from __future__ import annotations

import hashlib
import io
import json

import pytest


def test_upload_batch_adds_deduplicates_and_freezes_files(tmp_path) -> None:
    from backend.scan_grading.workspace import (
        FrozenUploadBatchError,
        ScanGradingWorkspace,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    content = b"anonymous scan image"
    digest = hashlib.sha256(content).hexdigest()

    first = workspace.add_upload(
        7,
        filename="七年级 1 班-001.jpg",
        media_type="image/jpeg",
        content_sha256=digest,
        source=io.BytesIO(content),
    )
    duplicate = workspace.add_upload(
        7,
        filename="renamed-copy.jpg",
        media_type="image/jpeg",
        content_sha256=digest,
        source=io.BytesIO(content),
    )

    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["file"]["id"] == first["file"]["id"]
    assert workspace.get_workspace(7)["upload_batch"] == {
        "batch_id": workspace.get_workspace(7)["upload_batch"]["batch_id"],
        "revision": 1,
        "state": "draft",
        "files": [
            {
                "id": first["file"]["id"],
                "name": "七年级 1 班-001.jpg",
                "media_type": "image/jpeg",
                "size_bytes": len(content),
                "sha256_prefix": digest[:12],
                "added_at": first["file"]["added_at"],
            }
        ],
        "file_count": 1,
        "total_bytes": len(content),
        "frozen_at": None,
    }

    frozen = workspace.freeze_uploads(7, expected_revision=1)
    assert frozen["state"] == "frozen"
    assert frozen["revision"] == 2

    with pytest.raises(FrozenUploadBatchError):
        workspace.add_upload(
            7,
            filename="002.jpg",
            media_type="image/jpeg",
            content_sha256=hashlib.sha256(b"second").hexdigest(),
            source=io.BytesIO(b"second"),
        )

    assert not list((tmp_path / "exams").rglob("*.tmp"))


def test_upload_batch_removes_and_clears_only_draft_files(tmp_path) -> None:
    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    added = []
    for name, content in (("front.png", b"front"), ("back.png", b"back")):
        added.append(
            workspace.add_upload(
                9,
                filename=name,
                media_type="image/png",
                content_sha256=hashlib.sha256(content).hexdigest(),
                source=io.BytesIO(content),
            )["file"]
        )

    after_remove = workspace.remove_upload(9, added[0]["id"], expected_revision=2)
    assert after_remove["revision"] == 3
    assert [item["id"] for item in after_remove["files"]] == [added[1]["id"]]

    after_clear = workspace.clear_uploads(9, expected_revision=3)
    assert after_clear["revision"] == 4
    assert after_clear["files"] == []
    assert list((tmp_path / "exams").rglob("*.png")) == []


def test_upload_batch_rejects_unsupported_or_oversized_files_without_publishing(tmp_path) -> None:
    from backend.scan_grading.workspace import (
        InvalidScanUploadError,
        ScanGradingWorkspace,
        ScanUploadTooLargeError,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        max_file_bytes=4,
    )

    with pytest.raises(InvalidScanUploadError):
        workspace.add_upload(
            2,
            filename="notes.txt",
            media_type="text/plain",
            content_sha256=hashlib.sha256(b"text").hexdigest(),
            source=io.BytesIO(b"text"),
        )
    with pytest.raises(ScanUploadTooLargeError):
        workspace.add_upload(
            2,
            filename="paper.pdf",
            media_type="application/pdf",
            content_sha256=hashlib.sha256(b"12345").hexdigest(),
            source=io.BytesIO(b"12345"),
        )

    assert workspace.get_workspace(2)["upload_batch"]["files"] == []
    assert not list((tmp_path / "exams").rglob("*.tmp"))


def test_preflight_projection_hides_paths_and_saves_revisioned_decisions(tmp_path) -> None:
    from backend.scan_grading.workspace import (
        ScanGradingWorkspace,
        UploadBatchRevisionError,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    for name, content in (("front.jpg", b"front"), ("back.jpg", b"back")):
        workspace.add_upload(
            3,
            filename=name,
            media_type="image/jpeg",
            content_sha256=hashlib.sha256(content).hexdigest(),
            source=io.BytesIO(content),
        )
    workspace.freeze_uploads(3, expected_revision=2)
    scan_files = sorted((tmp_path / "exams").rglob("*.jpg"))
    analysis_path = tmp_path / "templates" / "session_3" / "scan_analysis_latest.json"
    analysis_path.write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "front_image": str(scan_files[1]),
                        "back_image": str(scan_files[0]),
                        "student_name": "学生甲",
                        "student_id": 11,
                        "detected_name": "学生中",
                        "source_label": "001",
                        "match_method": "fuzzy",
                        "match_score": 0.72,
                    }
                ],
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "issue_type": "unmatched",
                        "message": "未匹配 C:/private/scan.jpg",
                        "front_image": str(scan_files[1]),
                        "back_image": str(scan_files[0]),
                        "detected_name": "未知",
                        "source_label": "002",
                    }
                ],
                "absent_students": [{"id": 12, "name": "学生乙", "student_code": "S012"}],
                "warnings": ["有 1 份需要处理"],
                "total_pages": 4,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    preflight = workspace.get_preflight(3)
    assert preflight["summary"] == {
        "auto_matched": 1,
        "issues": 1,
        "absent_candidates": 1,
        "total_pages": 4,
    }
    assert preflight["revision"] == 0
    assert preflight["groups"][0]["front_media_url"].startswith("/api/")
    assert preflight["issues"][0]["message"] == "扫描文件需要人工处理"
    assert str(tmp_path) not in json.dumps(preflight, ensure_ascii=False)

    saved = workspace.save_decisions(
        3,
        expected_revision=0,
        valid_student_ids={11, 12},
        decisions=[
            {
                "target_type": "group",
                "target_id": preflight["groups"][0]["id"],
                "action": "match",
                "student_id": 12,
            },
            {
                "target_type": "issue",
                "target_id": "issue-1",
                "action": "invalid",
            },
        ],
    )
    assert saved["revision"] == 1
    assert saved["pending_issue_count"] == 0

    with pytest.raises(UploadBatchRevisionError):
        workspace.save_decisions(
            3,
            expected_revision=0,
            valid_student_ids={11, 12},
            decisions=[],
        )
