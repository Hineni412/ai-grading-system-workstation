from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成文字速记密码-足够长-001"


def _unlocked(tmp_path: Path) -> tuple[VaultService, str]:
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-quick-inbox",
    )
    return service, str(initialized["session_token"])


def test_text_inbox_is_available_while_voice_and_external_transcription_are_off(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    item = service.quick_inbox.create_text(
        token=token,
        operation_id="create-quick-text",
        text="合成学生完成了步骤卡。下周复查课堂表现。",
        subject_id=None,
    )
    assert len(item["fragments"]) == 2
    assert item["voice_inbox_available"] is False
    assert item["external_transcription_allowed"] is False
    assert item["physical_request_count"] == 0
    assert service.support.list_subjects(token=token)["items"] == []


def test_teacher_edit_then_confirm_creates_exactly_one_formal_record(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = service.support.create_subject(
        token=token,
        operation_id="create-quick-subject",
        source_student_id="synthetic-quick-001",
        display_name="合成速记学生",
        class_label="合成一班",
    )
    item = service.quick_inbox.create_text(
        token=token,
        operation_id="create-quick-confirm",
        text="教师看到合成学生主动核对任务。",
        subject_id=str(subject["subject_id"]),
    )
    fragment = dict(item["fragments"][0])
    fragment["text"] = "教师看到合成学生在一次任务中主动核对步骤。"
    edited = service.quick_inbox.update_fragments(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
        operation_id="edit-quick-fragment",
        revision=int(item["revision"]),
        fragments=[fragment],
    )
    result = service.quick_inbox.confirm(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
        operation_id="confirm-quick-support",
        fragment_id=str(edited["fragments"][0]["fragment_id"]),
        target_kind="support_record",
        target_options={
            "record_kind": "fact",
            "scene": "合成课堂",
            "observed_at": "2026-08-03T09:00:00+08:00",
        },
    )
    replay = service.quick_inbox.confirm(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
        operation_id="confirm-quick-support",
        fragment_id=str(edited["fragments"][0]["fragment_id"]),
        target_kind="support_record",
        target_options={},
    )
    assert result == replay
    records = service.support.list_records(
        token=token,
        subject_id=str(subject["subject_id"]),
    )
    assert len(records["items"]) == 1
    stored = service.quick_inbox.get(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
    )
    assert stored["original_text"] is None
    assert stored["fragments"] == []


def test_cancel_clears_sensitive_text_without_claiming_audio_cleanup(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    item = service.quick_inbox.create_text(
        token=token,
        operation_id="create-quick-cancel",
        text="仅用于取消检查的合成文字。",
        subject_id=None,
    )
    cancelled = service.quick_inbox.cancel(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
        operation_id="cancel-quick-text",
    )
    assert cancelled["audio_cleanup_state"] == "not_applicable"
    stored = service.quick_inbox.get(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
    )
    assert stored["state"] == "cancelled"
    assert stored["original_text"] is None


def test_confirming_one_fragment_keeps_other_fragments_for_teacher_review(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = service.support.create_subject(
        token=token,
        operation_id="create-multi-fragment-subject",
        source_student_id="synthetic-multi-001",
        display_name="合成多片段学生",
        class_label=None,
    )
    item = service.quick_inbox.create_text(
        token=token,
        operation_id="create-multi-fragment-text",
        text="第一句是合成事实。第二句是合成自述。",
        subject_id=str(subject["subject_id"]),
    )
    service.quick_inbox.confirm(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
        operation_id="confirm-first-fragment-only",
        fragment_id=str(item["fragments"][0]["fragment_id"]),
        target_kind="support_record",
        target_options={
            "record_kind": "fact",
            "scene": "合成速记",
            "observed_at": "2026-08-03T09:00:00+08:00",
        },
    )
    remaining = service.quick_inbox.get(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
    )
    assert remaining["state"] == "draft"
    assert [value["text"] for value in remaining["fragments"]] == [
        "第二句是合成自述。"
    ]


def test_interrupted_quick_confirmation_resumes_without_duplicate_target(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = service.support.create_subject(
        token=token,
        operation_id="create-recovery-subject",
        source_student_id="synthetic-recovery-001",
        display_name="合成恢复学生",
        class_label=None,
    )
    item = service.quick_inbox.create_text(
        token=token,
        operation_id="create-recovery-quick",
        text="合成中断恢复事实。",
        subject_id=str(subject["subject_id"]),
    )
    fragment = item["fragments"][0]
    entity_id = f"{item['inbox_item_id']}:{fragment['fragment_id']}"
    service.quick_inbox._claim_confirmation(
        entity_kind="quick_inbox",
        entity_id=entity_id,
        operation_id="interrupted-quick-confirm",
        requested_target_kind="support_record",
    )
    created = service.support.create_record(
        token=token,
        operation_id=(
            "quick-confirm-target-"
            + hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:32]
        ),
        subject_id=str(subject["subject_id"]),
        record_kind="fact",
        content=str(fragment["text"]),
        scene="合成恢复",
        source="合成故障注入",
        basis=None,
        counterexample=None,
        category="general",
        observed_at="2026-08-03T09:00:00+08:00",
        review_at=None,
        expires_at=None,
    )
    resumed = service.quick_inbox.confirm(
        token=token,
        inbox_item_id=str(item["inbox_item_id"]),
        operation_id="resumed-quick-confirm",
        fragment_id=str(fragment["fragment_id"]),
        target_kind="support_record",
        target_options={},
    )

    assert resumed["target_id"] == created["record_id"]
    assert len(service.support.list_records(
        token=token,
        subject_id=str(subject["subject_id"]),
    )["items"]) == 1
