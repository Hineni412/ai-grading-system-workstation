from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
def _service(tmp_path: Path) -> tuple[VaultService, str]:
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
    service.ensure_plaintext_ready()
    return service, ""


def test_create_plan_and_action_are_plaintext_and_idempotent(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    plan = service.actions.create_plan(
        token=token,
        operation_id="plan-create-001",
        title="合成家长会准备",
        description="只用于 B02 行动账本检查",
        final_deadline="2026-08-10T17:00:00+08:00",
    )
    replay = service.actions.create_plan(
        token=token,
        operation_id="plan-create-001",
        title="不会创建第二份",
        description=None,
        final_deadline=None,
    )
    action = service.actions.create_action(
        token=token,
        operation_id="action-create-001",
        plan_id=str(plan["plan_id"]),
        title="合成行动：准备议程",
        details="不含真实学校或学生信息",
        due_at="2026-08-08T16:00:00+08:00",
        depends_on_action_ids=[],
    )

    assert replay["plan_id"] == plan["plan_id"]
    assert action["status"] == "pending"
    raw = service.database.database_path.read_bytes()
    assert "合成家长会准备".encode("utf-8") in raw
    assert "合成行动：准备议程".encode("utf-8") in raw


def test_waiting_requires_review_and_revision_conflicts_are_rejected(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    plan = service.actions.create_plan(
        token=token,
        operation_id="plan-create-002",
        title="合成回执收集",
        description=None,
        final_deadline=None,
    )
    action = service.actions.create_action(
        token=token,
        operation_id="action-create-002",
        plan_id=str(plan["plan_id"]),
        title="等待合成回复",
        details=None,
        due_at=None,
        depends_on_action_ids=[],
    )

    with pytest.raises(VaultError) as missing:
        service.actions.update_action(
            token=token,
            action_id=str(action["action_id"]),
            operation_id="action-update-missing",
            revision=1,
            status="waiting",
            due_at=None,
            waiting_for_kind=None,
            review_at=None,
            completion_result=None,
            reason=None,
        )
    assert missing.value.code == "action_waiting_details_required"

    updated = service.actions.update_action(
        token=token,
        action_id=str(action["action_id"]),
        operation_id="action-update-waiting",
        revision=1,
        status="waiting",
        due_at=None,
        waiting_for_kind="合成家长回复",
        review_at="2026-08-05T09:00:00+08:00",
        completion_result=None,
        reason=None,
    )
    assert updated["revision"] == 2
    with pytest.raises(VaultError) as conflict:
        service.actions.update_action(
            token=token,
            action_id=str(action["action_id"]),
            operation_id="action-update-stale",
            revision=1,
            status="completed",
            due_at=None,
            waiting_for_kind=None,
            review_at=None,
            completion_result="已经完成",
            reason=None,
        )
    assert conflict.value.code == "vault_revision_conflict"


def test_deadline_shift_moves_only_unfinished_actions_and_dashboard_is_consistent(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    base_deadline = datetime.fromisoformat("2026-08-10T09:00:00+08:00")
    plan = service.actions.create_plan(
        token=token,
        operation_id="plan-create-003",
        title="合成会议任务",
        description=None,
        final_deadline=base_deadline.isoformat(),
    )
    active = service.actions.create_action(
        token=token,
        operation_id="action-create-active",
        plan_id=str(plan["plan_id"]),
        title="未完成行动",
        details=None,
        due_at=(base_deadline - timedelta(days=2)).isoformat(),
        depends_on_action_ids=[],
    )
    completed = service.actions.create_action(
        token=token,
        operation_id="action-create-complete",
        plan_id=str(plan["plan_id"]),
        title="已完成行动",
        details=None,
        due_at=(base_deadline - timedelta(days=3)).isoformat(),
        depends_on_action_ids=[],
    )
    completed = service.actions.update_action(
        token=token,
        action_id=str(completed["action_id"]),
        operation_id="action-complete",
        revision=1,
        status="completed",
        due_at=str(completed["due_at"]),
        waiting_for_kind=None,
        review_at=None,
        completion_result="合成结果",
        reason=None,
    )

    service.actions.update_plan(
        token=token,
        plan_id=str(plan["plan_id"]),
        operation_id="plan-shift-deadline",
        revision=1,
        title=str(plan["title"]),
        description=None,
        final_deadline=(base_deadline + timedelta(days=2)).isoformat(),
    )
    shifted = service.actions.get_action(
        token=token,
        action_id=str(active["action_id"]),
    )
    unchanged = service.actions.get_action(
        token=token,
        action_id=str(completed["action_id"]),
    )
    dashboard = service.actions.dashboard(
        token=token,
        as_of="2026-08-09T08:00:00+08:00",
    )

    assert datetime.fromisoformat(str(shifted["due_at"])) == (
        datetime.fromisoformat(str(active["due_at"])) + timedelta(days=2)
    )
    assert unchanged["due_at"] == completed["due_at"]
    assert any(
        item["action_id"] == active["action_id"]
        for item in dashboard["upcoming"]
    )
