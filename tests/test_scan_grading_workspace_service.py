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
    frozen = workspace.freeze_uploads(3, expected_revision=2)
    scan_files = sorted((tmp_path / "exams").rglob("*.jpg"))
    analysis_path = tmp_path / "templates" / "session_3" / "scan_analysis_latest.json"
    analysis_path.write_text(
        json.dumps(
            {
                "scan_batch_id": frozen["batch_id"],
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


def test_starting_a_new_batch_archives_the_previous_preflight(tmp_path) -> None:
    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    content = b"old scan"
    workspace.add_upload(
        4, filename="old.jpg", media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(), source=io.BytesIO(content),
    )
    old_batch = workspace.freeze_uploads(4, expected_revision=1)
    session_dir = tmp_path / "templates" / "session_4"
    for name in (
        "scan_analysis_latest.json", "scan_decisions_state.json",
        "scan_manual_decisions_latest.json",
    ):
        (session_dir / name).write_text("{}" if name != "scan_manual_decisions_latest.json" else "[]", encoding="utf-8")

    new_batch = workspace.start_new_upload_batch(4)

    assert new_batch["state"] == "draft"
    assert new_batch["batch_id"] != old_batch["batch_id"]
    for name in (
        "scan_analysis_latest.json", "scan_decisions_state.json",
        "scan_manual_decisions_latest.json",
    ):
        assert not (session_dir / name).exists()
        assert (session_dir / "scan_history" / old_batch["batch_id"] / name).exists()


def test_new_batch_archive_failure_keeps_previous_batch_intact(tmp_path, monkeypatch) -> None:
    import backend.scan_grading.workspace as workspace_module
    from backend.scan_grading.workspace import (
        ScanGradingWorkspace,
        ScanGradingWorkspaceError,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    content = b"old scan"
    workspace.add_upload(
        5,
        filename="old.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    old_batch = workspace.freeze_uploads(5, expected_revision=1)
    session_dir = tmp_path / "templates" / "session_5"
    names = (
        "scan_analysis_latest.json",
        "scan_decisions_state.json",
        "scan_manual_decisions_latest.json",
    )
    for name in names:
        (session_dir / name).write_text("{}", encoding="utf-8")
    original_replace = workspace_module.os.replace

    failure_injected = False

    def fail_second_archive(source, target):
        nonlocal failure_injected
        if (
            not failure_injected
            and "scan_history" in str(target)
            and str(target).endswith("scan_decisions_state.json")
        ):
            failure_injected = True
            raise OSError("injected archive failure")
        return original_replace(source, target)

    monkeypatch.setattr(workspace_module.os, "replace", fail_second_archive)
    with pytest.raises(ScanGradingWorkspaceError):
        workspace.start_new_upload_batch(5)

    loaded = workspace.get_workspace(5)
    assert loaded["upload_batch"]["batch_id"] == old_batch["batch_id"]
    assert all((session_dir / name).is_file() for name in names)


def test_preflight_rejects_analysis_from_previous_batch(tmp_path) -> None:
    from backend.scan_grading.workspace import (
        ScanGradingWorkspace,
        ScanGradingWorkspaceError,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    old_content = b"old scan"
    workspace.add_upload(
        6,
        filename="old.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(old_content).hexdigest(),
        source=io.BytesIO(old_content),
    )
    old_batch = workspace.freeze_uploads(6, expected_revision=1)
    workspace.start_new_upload_batch(6)
    new_content = b"new scan"
    workspace.add_upload(
        6,
        filename="new.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(new_content).hexdigest(),
        source=io.BytesIO(new_content),
    )
    workspace.freeze_uploads(6, expected_revision=1)
    session_dir = tmp_path / "templates" / "session_6"
    (session_dir / "scan_analysis_latest.json").write_text(
        json.dumps(
            {
                "scan_batch_id": old_batch["batch_id"],
                "groups": [],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ScanGradingWorkspaceError):
        workspace.get_preflight(6)


def test_restart_rolls_back_interrupted_batch_archive(tmp_path, monkeypatch) -> None:
    import backend.scan_grading.workspace as workspace_module
    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    content = b"restart scan"
    workspace.add_upload(
        8,
        filename="restart.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    old_batch = workspace.freeze_uploads(8, expected_revision=1)
    session_dir = tmp_path / "templates" / "session_8"
    names = (
        "scan_analysis_latest.json",
        "scan_decisions_state.json",
        "scan_manual_decisions_latest.json",
    )
    for name in names:
        (session_dir / name).write_text("{}", encoding="utf-8")
    original_replace = workspace_module.os.replace
    interrupted = False

    def exit_after_first_archive(source, target):
        nonlocal interrupted
        result = original_replace(source, target)
        if not interrupted and "scan_history" in str(target):
            interrupted = True
            raise SystemExit("injected process exit")
        return result

    monkeypatch.setattr(workspace_module.os, "replace", exit_after_first_archive)
    with pytest.raises(SystemExit):
        workspace.start_new_upload_batch(8)
    monkeypatch.setattr(workspace_module.os, "replace", original_replace)

    restarted = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    loaded = restarted.get_workspace(8)
    assert loaded["upload_batch"]["batch_id"] == old_batch["batch_id"]
    assert all((session_dir / name).is_file() for name in names)
    assert not (session_dir / "scan_batch_transition.json").exists()


def test_restart_finishes_archive_after_new_manifest_was_published(
    tmp_path,
    monkeypatch,
) -> None:
    from pathlib import Path

    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    content = b"committed scan"
    workspace.add_upload(
        9,
        filename="committed.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    old_batch = workspace.freeze_uploads(9, expected_revision=1)
    session_dir = tmp_path / "templates" / "session_9"
    names = (
        "scan_analysis_latest.json",
        "scan_decisions_state.json",
        "scan_manual_decisions_latest.json",
    )
    for name in names:
        (session_dir / name).write_text("{}", encoding="utf-8")
    original_unlink = Path.unlink

    def exit_before_transition_cleanup(path, *args, **kwargs):
        if path.name == "scan_batch_transition.json":
            raise SystemExit("injected process exit")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", exit_before_transition_cleanup)
    with pytest.raises(SystemExit):
        workspace.start_new_upload_batch(9)
    monkeypatch.setattr(Path, "unlink", original_unlink)

    restarted = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    loaded = restarted.get_workspace(9)
    assert loaded["upload_batch"]["batch_id"] != old_batch["batch_id"]
    for name in names:
        assert not (session_dir / name).exists()
        assert (session_dir / "scan_history" / old_batch["batch_id"] / name).is_file()
    assert not (session_dir / "scan_batch_transition.json").exists()
