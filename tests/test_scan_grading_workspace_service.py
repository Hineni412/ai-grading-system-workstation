from __future__ import annotations

import hashlib
import io
import json

import pytest


def test_released_scans_cannot_be_reanalyzed_and_replacement_resets_receipt(tmp_path):
    from backend.scan_grading.workspace import ScanGradingWorkspace
    from backend.files.session_originals import ScanSourcesReleased, receipt_path, originals_state
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


def _upload_scan(workspace, session_id, name, payload, *, append=False):
    data = _jpeg(payload)
    return workspace.add_upload(
        session_id,
        filename=name,
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(data).hexdigest(),
        source=io.BytesIO(data),
        append=append,
    )


def _frozen_workspace(tmp_path, session_id, *payloads):
    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        data_root=tmp_path,
    )
    for index, payload in enumerate(payloads):
        _upload_scan(workspace, session_id, f"scan{index}.jpg", payload)
    return workspace, workspace.freeze_uploads(
        session_id, expected_revision=len(payloads)
    )


def _batch_file(tmp_path, session_id, batch_id, sha256_prefix):
    files_dir = (
        tmp_path / "exams" / f"session_{session_id}"
        / "scan_batches" / batch_id / "files"
    )
    return next(
        path for path in files_dir.iterdir()
        if path.name.startswith(sha256_prefix)
    )


def _write_analysis(
    tmp_path, session_id, batch, *, groups=(), issues=(), upload_revision=None
):
    session_dir = tmp_path / "templates" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_batch_id": batch["batch_id"],
        "groups": list(groups),
        "issues": list(issues),
        "absent_students": [],
        "warnings": [],
        "total_pages": len(groups) + len(issues),
    }
    if upload_revision is not None:
        payload["scan_upload_revision"] = int(upload_revision)
    path = session_dir / "scan_analysis_latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _group(source_label, front_image, **overrides):
    group = {
        "source_label": source_label,
        "front_image": str(front_image),
        "back_image": None,
        "student_id": 1,
        "student_name": "学生甲",
        "match_method": "exact",
        "match_score": 1.0,
    }
    group.update(overrides)
    return group


def test_frozen_batch_accepts_appended_files(tmp_path):
    workspace, batch = _frozen_workspace(tmp_path, 7, b"first")

    added = _upload_scan(workspace, 7, "late.jpg", b"late", append=True)

    assert added["duplicate"] is False
    assert added["file"]["appended"] is True
    state = workspace.get_workspace(7)["upload_batch"]
    assert state["batch_id"] == batch["batch_id"]
    assert state["state"] == "frozen"
    assert state["revision"] == batch["revision"] + 1
    assert [item["appended"] for item in state["files"]] == [False, True]


def test_append_requires_a_frozen_batch(tmp_path):
    from backend.scan_grading.workspace import (
        ScanGradingWorkspace,
        ScanGradingWorkspaceError,
    )

    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        data_root=tmp_path,
    )
    with pytest.raises(ScanGradingWorkspaceError):
        _upload_scan(workspace, 7, "late.jpg", b"late", append=True)
    _upload_scan(workspace, 7, "scan0.jpg", b"first")
    with pytest.raises(ScanGradingWorkspaceError):
        _upload_scan(workspace, 7, "late.jpg", b"late", append=True)


def test_appended_upload_deduplicates_by_content(tmp_path):
    workspace, batch = _frozen_workspace(tmp_path, 7, b"same")

    duplicate = _upload_scan(workspace, 7, "copy.jpg", b"same", append=True)

    assert duplicate["duplicate"] is True
    state = workspace.get_workspace(7)["upload_batch"]
    assert state["file_count"] == 1
    assert state["revision"] == batch["revision"]


def test_frozen_batch_only_removes_appended_files(tmp_path):
    from backend.scan_grading.workspace import FrozenUploadBatchError

    workspace, batch = _frozen_workspace(tmp_path, 7, b"first")
    added = _upload_scan(workspace, 7, "late.jpg", b"late", append=True)
    revision = workspace.get_workspace(7)["upload_batch"]["revision"]
    original_id = batch["files"][0]["id"]

    with pytest.raises(FrozenUploadBatchError):
        workspace.remove_upload(
            7, original_id, expected_revision=revision, append=True
        )
    with pytest.raises(FrozenUploadBatchError):
        workspace.remove_upload(7, added["file"]["id"], expected_revision=revision)

    updated = workspace.remove_upload(
        7, added["file"]["id"], expected_revision=revision, append=True
    )
    assert updated["file_count"] == 1
    assert updated["files"][0]["id"] == original_id


def test_appended_files_mark_preflight_stale_until_rerun(tmp_path):
    workspace, batch = _frozen_workspace(tmp_path, 7, b"first")
    first = _batch_file(
        tmp_path, 7, batch["batch_id"], batch["files"][0]["sha256_prefix"]
    )
    group = _group("scan0.jpg", first)
    # 旧格式预检结果没有 scan_upload_revision：没有新增文件时仍视为最新。
    _write_analysis(tmp_path, 7, batch, groups=[group])
    assert workspace.get_preflight(7)["input_changed"] is False

    added = _upload_scan(workspace, 7, "late.jpg", b"late", append=True)
    stale = workspace.get_preflight(7)
    assert stale["input_changed"] is True
    assert stale["appended_file_count"] == 1

    second = _batch_file(
        tmp_path, 7, batch["batch_id"], added["file"]["sha256_prefix"]
    )
    _write_analysis(
        tmp_path, 7, batch,
        groups=[group, _group("late.jpg", second, student_id=2, student_name="学生乙")],
        upload_revision=batch["revision"] + 1,
    )
    assert workspace.get_preflight(7)["input_changed"] is False


def test_rerun_preflight_migrates_decisions_by_stable_identity(tmp_path):
    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace, batch = _frozen_workspace(tmp_path, 7, b"first", b"second")
    first = _batch_file(
        tmp_path, 7, batch["batch_id"], batch["files"][0]["sha256_prefix"]
    )
    second = _batch_file(
        tmp_path, 7, batch["batch_id"], batch["files"][1]["sha256_prefix"]
    )
    group = _group("scan0.jpg", first)
    paired_issue = {
        "issue_id": "image_pair_1_2",
        "issue_type": "unpaired_image",
        "source_label": "scan0.jpg 第 1-2 页",
        "front_image": str(first),
        "back_image": str(second),
    }
    named_issue = {
        "issue_id": "scan0_p1_2",
        "issue_type": "unknown_name",
        "source_label": "scan0.jpg",
        "front_image": str(first),
        "back_image": None,
    }
    _write_analysis(
        tmp_path, 7, batch,
        groups=[group], issues=[paired_issue, named_issue],
        upload_revision=batch["revision"],
    )
    group_id = ScanGradingWorkspace._group_id(group)
    saved = workspace.save_decisions(
        7,
        expected_revision=0,
        valid_student_ids={1, 2},
        decisions=[
            {"target_type": "group", "target_id": group_id, "action": "invalid"},
            {"target_type": "issue", "target_id": "image_pair_1_2", "action": "pending"},
        ],
    )
    assert saved["revision"] == 1
    # 旧版本写下的决定内部记录没有来源字段；名字派生的 issue_id 稳定可保留，
    # 序号派生的 issue_id 可能指向别的答卷，必须丢弃。
    state_path = tmp_path / "templates" / "session_7" / "scan_decisions_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["public_decisions"].extend([
        {"target_type": "issue", "target_id": "scan0_p1_2", "action": "invalid"},
        {"target_type": "issue", "target_id": "image_orphan_1", "action": "pending"},
    ])
    state["internal_decisions"].extend([
        {"issue_id": "scan0_p1_2", "action": "invalid"},
        {"issue_id": "image_orphan_1", "action": "pending"},
    ])
    state_path.write_text(json.dumps(state), encoding="utf-8")

    added = _upload_scan(workspace, 7, "late.jpg", b"late", append=True)
    third = _batch_file(
        tmp_path, 7, batch["batch_id"], added["file"]["sha256_prefix"]
    )
    rerun_issues = [
        {**paired_issue, "issue_id": "image_pair_3_4"},
        named_issue,
        {
            "issue_id": "image_orphan_1",
            "issue_type": "orphan_image",
            "source_label": "late.jpg",
            "front_image": str(third),
            "back_image": None,
        },
    ]
    _write_analysis(
        tmp_path, 7, batch,
        groups=[group, _group("late.jpg", third, student_id=2, student_name="学生乙")],
        issues=rerun_issues,
        upload_revision=batch["revision"] + 1,
    )

    preflight = workspace.get_preflight(7)
    assert preflight["input_changed"] is False
    assert preflight["revision"] == 2
    decisions = {
        (d["target_type"], d["target_id"]): d["action"]
        for d in preflight["decisions"]
    }
    assert decisions == {
        ("group", group_id): "invalid",
        ("issue", "image_pair_3_4"): "pending",
        ("issue", "scan0_p1_2"): "invalid",
    }
    manual = json.loads(
        (tmp_path / "templates" / "session_7" / "scan_manual_decisions_latest.json")
        .read_text(encoding="utf-8")
    )
    assert {
        d.get("issue_id") for d in manual if "issue_id" in d
    } == {"image_pair_3_4", "scan0_p1_2"}
    assert any(d.get("group_source_label") == "scan0.jpg" for d in manual)

    # 重新预检后消失的答卷不再携带旧决定。
    _write_analysis(
        tmp_path, 7, batch,
        groups=[_group("late.jpg", third, student_id=2, student_name="学生乙")],
        issues=[],
        upload_revision=batch["revision"] + 1,
    )
    assert workspace.get_preflight(7)["decisions"] == []


def test_grading_start_requires_current_preflight_after_append(tmp_path):
    from backend.scan_grading.workspace import ScanPreflightOutdatedError

    workspace, batch = _frozen_workspace(tmp_path, 7, b"first")
    first = _batch_file(
        tmp_path, 7, batch["batch_id"], batch["files"][0]["sha256_prefix"]
    )
    _write_analysis(
        tmp_path, 7, batch,
        groups=[_group("scan0.jpg", first)],
        upload_revision=batch["revision"],
    )
    _upload_scan(workspace, 7, "late.jpg", b"late", append=True)
    revision = workspace.get_workspace(7)["upload_batch"]["revision"]

    with pytest.raises(ScanPreflightOutdatedError):
        workspace.prepare_start(
            7,
            grading_mode="ai",
            upload_revision=revision,
            decision_revision=0,
            confirm_pending_issues=True,
            enhance_images=True,
            max_workers=None,
            requests_per_minute=None,
        )
