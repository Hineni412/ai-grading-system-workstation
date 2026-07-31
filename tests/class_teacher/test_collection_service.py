from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.api.collection_schemas import (
    CollectionBoardResponse,
    MeetingInboxResponse,
)
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成收集板密码-足够长-001"


def _unlocked(tmp_path: Path) -> tuple[VaultService, str]:
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        migration_project_root=PROJECT_ROOT,
    )
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=paths,
        )
    )
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-collections",
    )
    token = str(initialized["session_token"])
    service.actions.save_calendar(
        token=token,
        operation_id="collection-calendar",
        revision=0,
        school_day_end="17:30",
        locked_dates=[],
        working_weekdays=[1, 2, 3, 4, 5],
    )
    return service, token


def _meeting(service: VaultService, token: str) -> dict[str, object]:
    return service.collections.import_meeting_notes(
        token=token,
        operation_id="import-synthetic-meeting",
        raw_text=(
            "2026-08-14 收齐合成回执并报年级\n"
            "2026-08-18 完成合成材料准备"
        ),
        reference_at="2026-08-03T09:00:00+08:00",
    )


def test_meeting_notes_split_into_different_deadlines_without_formal_actions(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    inbox = _meeting(service, token)

    MeetingInboxResponse.model_validate(inbox)
    assert len(inbox["drafts"]) == 2
    assert {
        item["deadline_date"] for item in inbox["drafts"]
    } == {"2026-08-14", "2026-08-18"}
    assert inbox["model_enabled"] is False
    assert inbox["physical_request_count"] == 0
    assert service.actions.list_plans(token=token)["items"] == []
    assert service.actions.list_actions(token=token)["items"] == []


def test_full_draft_edit_supports_split_delete_and_date_conflict(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    inbox = _meeting(service, token)
    first = dict(inbox["drafts"][0])
    split = {
        **first,
        "meeting_draft_id": "synthetic-split-draft",
        "title": "拆分后的合成任务",
        "objective": "2026-08-14 拆分后的合成任务",
    }
    updated = service.collections.update_meeting_inbox(
        token=token,
        inbox_id=str(inbox["inbox_id"]),
        operation_id="update-split-delete",
        revision=int(inbox["revision"]),
        drafts=[first, split],
        delete_source_after_confirm=True,
    )
    assert len(updated["drafts"]) == 2
    assert updated["drafts"][1]["title"] == "拆分后的合成任务"

    conflicting = dict(updated["drafts"][0])
    conflicting["final_deadline"] = None
    conflicting["unknowns"] = ["合并后的最终截止时间需要重新确认"]
    merged = service.collections.update_meeting_inbox(
        token=token,
        inbox_id=str(updated["inbox_id"]),
        operation_id="update-merged-conflict",
        revision=int(updated["revision"]),
        drafts=[conflicting],
        delete_source_after_confirm=True,
    )
    with pytest.raises(VaultError) as error:
        service.collections.confirm_meeting_inbox(
            token=token,
            inbox_id=str(merged["inbox_id"]),
            operation_id="confirm-conflicting-meeting",
            revision=int(merged["revision"]),
        )
    assert error.value.code == "meeting_deadline_unknown"


def test_confirm_is_atomic_idempotent_and_can_delete_source(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    inbox = _meeting(service, token)
    result = service.collections.confirm_meeting_inbox(
        token=token,
        inbox_id=str(inbox["inbox_id"]),
        operation_id="confirm-meeting-once",
        revision=int(inbox["revision"]),
    )
    replay = service.collections.confirm_meeting_inbox(
        token=token,
        inbox_id=str(inbox["inbox_id"]),
        operation_id="confirm-meeting-once",
        revision=int(inbox["revision"]),
    )
    stored = service.collections.get_meeting_inbox(
        token=token,
        inbox_id=str(inbox["inbox_id"]),
    )

    assert replay["plan_ids"] == result["plan_ids"]
    assert replay["action_ids"] == result["action_ids"]
    assert stored["source_deleted"] is True
    assert stored["raw_text"] is None
    assert len(service.actions.list_plans(token=token)["items"]) == 2
    assert len(service.actions.list_actions(token=token)["items"]) == 8


def test_collection_board_counts_and_reminders_preserve_identity_boundary(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    plan = service.actions.create_plan(
        token=token,
        operation_id="collection-plan",
        title="合成回执目标",
        description=None,
        final_deadline=None,
    )
    action = service.actions.create_action(
        token=token,
        operation_id="collection-action",
        plan_id=str(plan["plan_id"]),
        title="收齐合成回执",
        details=None,
        due_at=None,
        depends_on_action_ids=[],
    )
    board = service.collections.create_board(
        token=token,
        operation_id="create-collection-board",
        action_id=str(action["action_id"]),
        title="合成回执收集板",
        participant_refs=["synthetic-a", "synthetic-b", "synthetic-c"],
    )
    CollectionBoardResponse.model_validate(board)
    assert board["total_count"] == 3
    assert sum(board["counts"].values()) == 3
    assert "synthetic-a" not in board["group_reminder"]["content"]

    first = board["items"][0]
    updated = service.collections.update_collection_item(
        token=token,
        board_id=str(board["board_id"]),
        item_id=str(first["collection_item_id"]),
        operation_id="collection-item-submitted",
        revision=int(board["revision"]),
        status="submitted",
    )
    assert updated["counts"]["submitted"] == 1
    assert sum(updated["counts"].values()) == updated["total_count"]
    reminder = service.collections.individual_reminder(
        token=token,
        board_id=str(board["board_id"]),
        item_id=str(first["collection_item_id"]),
    )
    assert reminder["audience_ref"] == "synthetic-a"
    assert "synthetic-b" not in str(reminder)
    assert reminder["status"] == "unsent"

    with pytest.raises(VaultError) as conflict:
        service.collections.update_collection_item(
            token=token,
            board_id=str(board["board_id"]),
            item_id=str(first["collection_item_id"]),
            operation_id="collection-item-stale",
            revision=int(board["revision"]),
            status="completed",
        )
    assert conflict.value.code == "vault_revision_conflict"
    raw = service.database.database_path.read_bytes()
    assert b"synthetic-a" not in raw
    assert "合成回执收集板".encode("utf-8") not in raw
