from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成本地规划密码-足够长-001"


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
        operation_id="initialize-planning",
    )
    return service, str(initialized["session_token"])


def _configure_calendar(service: VaultService, token: str) -> None:
    service.actions.save_calendar(
        token=token,
        operation_id="planning-calendar",
        revision=0,
        school_day_end="17:30",
        locked_dates=["2026-08-12"],
        working_weekdays=[1, 2, 3, 4, 5],
    )


def test_local_receipt_draft_uses_calendar_without_model_request(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    _configure_calendar(service, token)

    draft = service.planning.create_draft(
        token=token,
        operation_id="planning-draft-receipt",
        raw_input="下周五收齐合成回执并报年级",
        reference_at="2026-08-03T09:00:00+08:00",
        final_deadline=None,
    )

    assert draft["template_kind"] == "receipt"
    assert draft["deadline_date"] == "2026-08-14"
    assert len(draft["actions"]) == 5
    assert all(item["due_at"] for item in draft["actions"])
    assert draft["send_preview"]["model_enabled"] is False
    assert draft["send_preview"]["physical_request_count"] == 0
    assert draft["communication_drafts"][0]["status"] == "unsent"
    assert draft["unknowns"] == []


def test_unknown_calendar_never_invents_action_times(tmp_path: Path) -> None:
    service, token = _unlocked(tmp_path)

    draft = service.planning.create_draft(
        token=token,
        operation_id="planning-draft-unknown-calendar",
        raw_input="下周五收齐合成回执并报年级",
        reference_at="2026-08-03T09:00:00+08:00",
        final_deadline=None,
    )

    assert draft["deadline_date"] == "2026-08-14"
    assert draft["final_deadline"] is None
    assert all(item["due_at"] is None for item in draft["actions"])
    assert any("放学时间" in item for item in draft["unknowns"])
    assert any("工作日" in item for item in draft["unknowns"])
    with pytest.raises(VaultError) as error:
        service.planning.confirm_draft(
            token=token,
            draft_id=str(draft["draft_id"]),
            operation_id="planning-confirm-unknown",
            revision=int(draft["revision"]),
            plan_title=str(draft["plan_title"]),
            actions=list(draft["actions"]),
        )
    assert error.value.code == "planning_deadline_unknown"


def test_sensitive_draft_is_local_only_and_requires_manual_sop(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    _configure_calendar(service, token)

    draft = service.planning.create_draft(
        token=token,
        operation_id="planning-draft-sensitive",
        raw_input="下周五处理合成学生疑似欺凌情况",
        reference_at="2026-08-03T09:00:00+08:00",
        final_deadline=None,
    )

    assert "高风险词：欺凌" in draft["sensitive_findings"]
    assert draft["send_preview"]["sent"] is False
    assert draft["send_preview"]["physical_request_count"] == 0
    with pytest.raises(VaultError) as error:
        service.planning.confirm_draft(
            token=token,
            draft_id=str(draft["draft_id"]),
            operation_id="planning-confirm-sensitive",
            revision=int(draft["revision"]),
            plan_title=str(draft["plan_title"]),
            actions=list(draft["actions"]),
        )
    assert error.value.code == "planning_requires_manual_sop"


def test_confirm_is_atomic_encrypted_and_idempotent(tmp_path: Path) -> None:
    service, token = _unlocked(tmp_path)
    _configure_calendar(service, token)
    draft = service.planning.create_draft(
        token=token,
        operation_id="planning-draft-confirm",
        raw_input="下周五收齐合成回执并报年级",
        reference_at="2026-08-03T09:00:00+08:00",
        final_deadline=None,
    )

    result = service.planning.confirm_draft(
        token=token,
        draft_id=str(draft["draft_id"]),
        operation_id="planning-confirm-once",
        revision=int(draft["revision"]),
        plan_title=str(draft["plan_title"]),
        actions=list(draft["actions"]),
    )
    replay = service.planning.confirm_draft(
        token=token,
        draft_id=str(draft["draft_id"]),
        operation_id="planning-confirm-once",
        revision=int(draft["revision"]),
        plan_title="不会生成第二份",
        actions=list(draft["actions"]),
    )

    assert replay["plan_id"] == result["plan_id"]
    assert replay["action_ids"] == result["action_ids"]
    assert result["physical_request_count"] == 0
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 5
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM communication_drafts"
            ).fetchone()[0]
            == 1
        )
    raw = service.database.database_path.read_bytes()
    assert "下周五收齐合成回执并报年级".encode("utf-8") not in raw
    assert "发出回执通知".encode("utf-8") not in raw


def test_communication_retention_uses_planned_action_date_not_draft_creation(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    _configure_calendar(service, token)
    draft = service.planning.create_draft(
        token=token,
        operation_id="planning-draft-retention",
        raw_input="下周五收齐合成回执并报年级",
        reference_at="2026-08-03T09:00:00+08:00",
        final_deadline=None,
    )
    service.planning.confirm_draft(
        token=token,
        draft_id=str(draft["draft_id"]),
        operation_id="planning-confirm-retention",
        revision=int(draft["revision"]),
        plan_title=str(draft["plan_title"]),
        actions=list(draft["actions"]),
    )
    vmk = bytes(service._require_session(token).vmk)
    with closing(service.database.connect()) as connection:
        with connection:
            connection.execute(
                """
                UPDATE communication_drafts
                SET created_at = '2020-01-01T00:00:00+00:00'
                """
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM communication_drafts"
        ).fetchone()[0] == 1

    service.touch(token=token)
    with closing(service.database.connect()) as connection:
        row = connection.execute(
            """
            SELECT a.payload_object_id
            FROM actions a
            JOIN communication_drafts cd ON cd.action_id = a.action_id
            """
        ).fetchone()
        assert row is not None
        payload, revision = service.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["payload_object_id"]),
        )
        payload["due_at"] = "2020-01-01T00:00:00+00:00"
        with connection:
            service.repository.put(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
                object_type="action",
                payload=payload,
                expected_revision=revision,
            )

    service.touch(token=token)
    with closing(service.database.connect()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM communication_drafts"
        ).fetchone()[0] == 0


def test_cancelled_draft_never_creates_formal_actions(tmp_path: Path) -> None:
    service, token = _unlocked(tmp_path)
    draft = service.planning.create_draft(
        token=token,
        operation_id="planning-draft-cancel",
        raw_input="安排一项合成普通任务",
        reference_at="2026-08-03T09:00:00+08:00",
        final_deadline="2026-08-14T17:30:00+08:00",
    )
    cancelled = service.planning.cancel_draft(
        token=token,
        draft_id=str(draft["draft_id"]),
        operation_id="planning-cancel-once",
        revision=int(draft["revision"]),
    )

    assert cancelled["status"] == "cancelled"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0
