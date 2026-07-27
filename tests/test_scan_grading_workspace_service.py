from __future__ import annotations

import hashlib
import io
import json

import pytest


def _jpeg(payload: bytes) -> bytes:
    return b"\xff\xd8\xff" + payload


def _png(payload: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + payload


def _frozen_workspace(tmp_path, session_id: int):
    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    content = _jpeg(f"batch-{session_id}".encode())
    workspace.add_upload(
        session_id,
        filename="scan.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    workspace.freeze_uploads(session_id, expected_revision=1)
    session_dir = tmp_path / "templates" / f"session_{session_id}"
    previous_manifest = json.loads(
        (session_dir / "scan_upload_batch.json").read_text(encoding="utf-8")
    )
    return workspace, session_dir, previous_manifest


def test_grading_start_rejects_preflight_from_a_previous_template(tmp_path) -> None:
    from PIL import Image

    from backend.scan_grading.workspace import (
        GradingConfigChangedError,
        ScanGradingWorkspace,
    )
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session("Template revision", "rubric.json", "answer.json")
    template_dir = tmp_path / "template-files"
    template_dir.mkdir()
    front_path = template_dir / "front.png"
    back_path = template_dir / "back.png"
    Image.new("RGB", (8, 8), "white").save(front_path)
    Image.new("RGB", (8, 8), "black").save(back_path)
    template_id = db.upsert_session_template(
        session_id,
        str(front_path),
        str(back_path),
    )
    db.mark_template_confirmed(session_id, True)

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        grading_db_path=db.db_path,
    )
    content = _jpeg(b"student scan")
    workspace.add_upload(
        session_id,
        filename="scan.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    frozen = workspace.freeze_uploads(session_id, expected_revision=1)
    analysis_path = (
        tmp_path
        / "templates"
        / f"session_{session_id}"
        / "scan_analysis_latest.json"
    )
    analysis_path.write_text(
        json.dumps(
            {
                "scan_batch_id": frozen["batch_id"],
                "config_revision": "config-revision",
                "template_id": template_id,
                "template_fingerprint": "f" * 64,
                "template_first_page_role": "front",
                "groups": [],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GradingConfigChangedError, match="template"):
        workspace.prepare_start(
            session_id,
            grading_mode="full_paper",
            upload_revision=int(frozen["revision"]),
            decision_revision=0,
            confirm_pending_issues=False,
            enhance_images=True,
            max_workers=None,
            requests_per_minute=None,
        )


def test_grading_start_preserves_portable_stored_template_paths(tmp_path) -> None:
    from PIL import Image

    from backend.scan_grading.workspace import ScanGradingWorkspace
    from db_manager import DBManager
    from template_upload_service import TemplateUploadService

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session("Portable", "rubric.json", "answer.json")
    templates_root = tmp_path / "templates"
    session_dir = templates_root / f"session_{session_id}"
    session_dir.mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(session_dir / "front.png")
    Image.new("RGB", (8, 8), "black").save(session_dir / "back.png")
    template_id = db.upsert_session_template(session_id, "front.png", "back.png")
    db.mark_template_confirmed(session_id, True)
    template = TemplateUploadService(templates_root).load_current(
        db=db,
        session_id=session_id,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=templates_root,
        grading_db_path=db.db_path,
    )
    content = _jpeg(b"student scan")
    workspace.add_upload(
        session_id,
        filename="scan.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    frozen = workspace.freeze_uploads(session_id, expected_revision=1)
    (session_dir / "scan_analysis_latest.json").write_text(
        json.dumps(
            {
                "scan_batch_id": frozen["batch_id"],
                "config_revision": "config-revision",
                "template_id": template_id,
                "template_fingerprint": template.template_fingerprint,
                "template_first_page_role": "front",
                "groups": [],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 0,
            }
        ),
        encoding="utf-8",
    )

    payload = workspace.prepare_start(
        session_id,
        grading_mode="full_paper",
        upload_revision=int(frozen["revision"]),
        decision_revision=0,
        confirm_pending_issues=False,
        enhance_images=True,
        max_workers=None,
        requests_per_minute=None,
    )

    assert payload["expected_front_template_path"] == "front.png"
    assert payload["expected_back_template_path"] == "back.png"


def test_upload_batch_adds_deduplicates_and_freezes_files(tmp_path) -> None:
    from backend.scan_grading.workspace import (
        FrozenUploadBatchError,
        ScanGradingWorkspace,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
    )
    content = _jpeg(b"anonymous scan image")
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
    for name, content in (("front.png", _png(b"front")), ("back.png", _png(b"back"))):
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
    for name, content in (("front.jpg", _jpeg(b"front")), ("back.jpg", _jpeg(b"back"))):
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
        "ready_to_grade": 1,
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
    content = _jpeg(b"old scan")
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
    content = _jpeg(b"old scan")
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
    old_content = _jpeg(b"old scan")
    workspace.add_upload(
        6,
        filename="old.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(old_content).hexdigest(),
        source=io.BytesIO(old_content),
    )
    old_batch = workspace.freeze_uploads(6, expected_revision=1)
    workspace.start_new_upload_batch(6)
    new_content = _jpeg(b"new scan")
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
    content = _jpeg(b"restart scan")
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
    content = _jpeg(b"committed scan")
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


@pytest.mark.parametrize("fault", ["same_batch_ids", "unrelated_current_manifest"])
def test_transition_recovery_rejects_ambiguous_manifest_identity_without_moving_files(
    tmp_path,
    fault: str,
) -> None:
    from backend.scan_grading.workspace import ScanGradingWorkspaceError

    workspace, session_dir, previous_manifest = _frozen_workspace(tmp_path, 10)
    next_manifest = {
        "batch_id": "b" * 32,
        "revision": 0,
        "state": "draft",
        "files": [],
        "frozen_at": None,
        "run_floor_id": 0,
    }
    source = session_dir / "scan_analysis_latest.json"
    target = (
        session_dir
        / "scan_history"
        / previous_manifest["batch_id"]
        / source.name
    )
    if fault == "same_batch_ids":
        next_manifest = dict(previous_manifest)
        source.write_text("old analysis", encoding="utf-8")
        expected_path, absent_path = source, target
    else:
        unrelated_manifest = dict(previous_manifest)
        unrelated_manifest["batch_id"] = "c" * 32
        (session_dir / "scan_upload_batch.json").write_text(
            json.dumps(unrelated_manifest),
            encoding="utf-8",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old analysis", encoding="utf-8")
        expected_path, absent_path = target, source
    transition_path = session_dir / "scan_batch_transition.json"
    transition_path.write_text(
        json.dumps(
            {
                "version": 2,
                "previous_batch_id": previous_manifest["batch_id"],
                "previous_manifest": previous_manifest,
                "next_manifest": next_manifest,
                "archive_files": [source.name],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ScanGradingWorkspaceError):
        workspace.get_workspace(10)

    assert transition_path.is_file()
    assert expected_path.read_text(encoding="utf-8") == "old analysis"
    assert not absent_path.exists()


def test_transition_recovery_validates_every_file_before_recovery_moves_any_file(
    tmp_path,
) -> None:
    from backend.scan_grading.workspace import ScanGradingWorkspaceError

    workspace, session_dir, previous_manifest = _frozen_workspace(tmp_path, 11)
    next_manifest = {
        "batch_id": "d" * 32,
        "revision": 0,
        "state": "draft",
        "files": [],
        "frozen_at": None,
        "run_floor_id": 0,
    }
    history_dir = (
        session_dir / "scan_history" / previous_manifest["batch_id"]
    )
    history_dir.mkdir(parents=True, exist_ok=True)
    archived_analysis = history_dir / "scan_analysis_latest.json"
    archived_analysis.write_text("archived analysis", encoding="utf-8")
    transition_path = session_dir / "scan_batch_transition.json"
    transition_path.write_text(
        json.dumps(
            {
                "version": 2,
                "previous_batch_id": previous_manifest["batch_id"],
                "previous_manifest": previous_manifest,
                "next_manifest": next_manifest,
                "archive_files": [
                    "scan_decisions_state.json",
                    "scan_analysis_latest.json",
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ScanGradingWorkspaceError):
        workspace.get_workspace(11)

    assert transition_path.is_file()
    assert archived_analysis.read_text(encoding="utf-8") == "archived analysis"
    assert not (session_dir / "scan_analysis_latest.json").exists()
    assert not (session_dir / "scan_decisions_state.json").exists()
    assert not (history_dir / "scan_decisions_state.json").exists()
