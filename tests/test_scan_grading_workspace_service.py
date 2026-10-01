from __future__ import annotations

import hashlib
import io
import json

import pytest


def test_released_scans_cannot_be_reanalyzed_and_replacement_resets_receipt(tmp_path):
    from backend.scan_grading.workspace import ScanGradingWorkspace
    from session_originals import ScanSourcesReleased, receipt_path, originals_state
    from types import SimpleNamespace
    workspace = ScanGradingWorkspace(exams_root=tmp_path / "exams", templates_root=tmp_path / "templates", data_root=tmp_path, job_manager=SimpleNamespace(list=lambda **kwargs: ([], 0)), replacement_reset=lambda sid: [])
    old = _jpeg(b"test old")
    workspace.add_upload(1, filename="old.jpg", media_type="image/jpeg", content_sha256=hashlib.sha256(old).hexdigest(), source=io.BytesIO(old))
    workspace.freeze_uploads(1, expected_revision=1)
    receipt_path(tmp_path, 1).write_text("broken", encoding="utf-8")
    with pytest.raises(ScanSourcesReleased):
        workspace.submit_scan_analysis(1, {})
    replacement = workspace.begin_replacement_upload(1)
    new = _jpeg(b"test replacement")
    uploaded = workspace.add_upload(1, replacement=True, filename="new.jpg", media_type="image/jpeg", content_sha256=hashlib.sha256(new).hexdigest(), source=io.BytesIO(new))
    workspace.commit_replacement_upload(1, expected_revision=1)
    assert originals_state(tmp_path, 1) == "complete"
    assert not receipt_path(tmp_path, 1).exists()


def _jpeg(payload: bytes) -> bytes:
    return b"\xff\xd8\xff" + payload


def test_new_batch_archive_failure_keeps_previous_batch_intact(
    tmp_path, monkeypatch
) -> None:
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
