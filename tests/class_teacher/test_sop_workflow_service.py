from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.api.sop_schemas import (
    AffairResponse,
    SopTemplateResponse,
)
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成SOP流程密码-足够长-001"


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
        operation_id="initialize-sop",
    )
    return service, str(initialized["session_token"])


def _steps(include_followup: bool = False) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = [
        {
            "key": "intake",
            "title": "记录合成事实",
            "details": "只记录可核对的合成事实",
            "required": True,
            "waivable": False,
            "safety_required": False,
            "depends_on": [],
        },
        {
            "key": "communication",
            "title": "完成合成沟通",
            "details": "由教师人工确认",
            "required": True,
            "waivable": True,
            "safety_required": False,
            "depends_on": ["intake"],
        },
        {
            "key": "safety_handoff",
            "title": "完成合成安全交接",
            "details": "安全必做，不可豁免",
            "required": True,
            "waivable": True,
            "safety_required": True,
            "depends_on": ["intake"],
        },
        {
            "key": "finish",
            "title": "核对合成结果",
            "details": "等待并行步骤共同完成",
            "required": True,
            "waivable": False,
            "safety_required": False,
            "depends_on": ["communication", "safety_handoff"],
        },
    ]
    if include_followup:
        steps.append(
            {
                "key": "followup",
                "title": "新增合成复查",
                "details": None,
                "required": False,
                "waivable": True,
                "safety_required": False,
                "depends_on": ["finish"],
            }
        )
    return steps


def _template(
    service: VaultService,
    token: str,
    *,
    version: int = 1,
    include_followup: bool = False,
) -> dict[str, object]:
    return service.sop.publish_template(
        token=token,
        operation_id=f"publish-sop-template-v{version}",
        template_key="synthetic-affair",
        version=version,
        title=f"合成事务模板 v{version}",
        steps=_steps(include_followup),
    )


def _affair(
    service: VaultService,
    token: str,
    template_version_id: str,
) -> dict[str, object]:
    return service.sop.create_affair(
        token=token,
        operation_id="create-sop-affair",
        template_version_id=template_version_id,
        title="合成事务",
        summary="不含真实学生或学校流程",
        participant_refs=["synthetic-student-a", "synthetic-student-b"],
    )


def _step(affair: dict[str, object], key: str) -> dict[str, object]:
    all_steps = [
        *list(affair["current_steps"]),
        *list(affair["completed_steps"]),
        *list(affair["preview_steps"]),
    ]
    return next(item for item in all_steps if item["key"] == key)


def _complete(
    service: VaultService,
    token: str,
    affair: dict[str, object],
    key: str,
    *,
    outcome: str = "completed",
) -> dict[str, object]:
    step = _step(affair, key)
    return service.sop.complete_step(
        token=token,
        affair_id=str(affair["affair_id"]),
        step_instance_id=str(step["step_instance_id"]),
        operation_id=f"complete-{key}-{outcome}",
        revision=int(step["revision"]),
        outcome=outcome,
        result=f"{key} 合成处理结果",
    )


def test_parallel_dependencies_safety_waiver_and_close(tmp_path: Path) -> None:
    service, token = _unlocked(tmp_path)
    template = _template(service, token)
    affair = _affair(service, token, str(template["template_version_id"]))

    SopTemplateResponse.model_validate(template)
    AffairResponse.model_validate(affair)
    assert [item["key"] for item in affair["current_steps"]] == ["intake"]
    assert len(service.actions.list_actions(token=token)["items"]) == 1

    affair = _complete(service, token, affair, "intake")
    assert {item["key"] for item in affair["current_steps"]} == {
        "communication",
        "safety_handoff",
    }
    assert len(service.actions.list_actions(token=token)["items"]) == 3

    safety = _step(affair, "safety_handoff")
    with pytest.raises(VaultError) as cannot_waive:
        service.sop.complete_step(
            token=token,
            affair_id=str(affair["affair_id"]),
            step_instance_id=str(safety["step_instance_id"]),
            operation_id="waive-safety-step",
            revision=int(safety["revision"]),
            outcome="waived",
            result="不能作为有效豁免",
        )
    assert cannot_waive.value.code == "sop_safety_step_cannot_be_waived"

    affair = _complete(service, token, affair, "communication", outcome="waived")
    affair = _complete(service, token, affair, "safety_handoff")
    assert [item["key"] for item in affair["current_steps"]] == ["finish"]
    affair = _complete(service, token, affair, "finish")
    closed = service.sop.close_affair(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="close-sop-affair",
        revision=int(affair["revision"]),
        closure_summary="合成事务已按必做步骤完成",
    )
    assert closed["state"] == "closed"


def test_template_versions_are_frozen_for_running_affairs(tmp_path: Path) -> None:
    service, token = _unlocked(tmp_path)
    version_one = _template(service, token, version=1)
    affair = _affair(service, token, str(version_one["template_version_id"]))
    version_two = _template(
        service,
        token,
        version=2,
        include_followup=True,
    )

    refreshed = service.sop.get_affair(
        token=token,
        affair_id=str(affair["affair_id"]),
    )
    assert refreshed["template_version"] == 1
    assert "followup" not in {
        item["key"]
        for item in [
            *refreshed["current_steps"],
            *refreshed["preview_steps"],
        ]
    }
    assert version_two["version"] == 2
    with pytest.raises(VaultError) as duplicate:
        service.sop.publish_template(
            token=token,
            operation_id="publish-duplicate-version",
            template_key="synthetic-affair",
            version=2,
            title="不能覆盖",
            steps=_steps(),
        )
    assert duplicate.value.code == "sop_template_version_exists"


def test_create_is_atomic_idempotent_and_body_is_encrypted(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    template = _template(service, token)
    affair = _affair(service, token, str(template["template_version_id"]))
    replay = service.sop.create_affair(
        token=token,
        operation_id="create-sop-affair",
        template_version_id=str(template["template_version_id"]),
        title="不会生成第二份",
        summary=None,
        participant_refs=["synthetic-student-c"],
    )

    assert replay["affair_id"] == affair["affair_id"]
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM affairs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 1
    raw = service.database.database_path.read_bytes()
    assert "合成事务".encode("utf-8") not in raw
    assert "synthetic-student-a".encode("utf-8") not in raw


def test_failed_create_rolls_back_affair_plan_and_participants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token = _unlocked(tmp_path)
    template = _template(service, token)
    original_put = service.repository.put

    def fail_on_participant(connection: Any, **kwargs: Any) -> int:
        if kwargs.get("object_type") == "affair_participant":
            raise RuntimeError("synthetic failure")
        return original_put(connection, **kwargs)

    monkeypatch.setattr(service.repository, "put", fail_on_participant)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        _affair(service, token, str(template["template_version_id"]))

    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM affairs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM affair_participants"
            ).fetchone()[0]
            == 0
        )


def test_close_reopen_new_occurrence_and_ai_suggestion_stays_advisory(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    template = _template(service, token)
    affair = _affair(service, token, str(template["template_version_id"]))
    affair = service.sop.record_decision(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="record-ai-suggestion",
        decision_kind="ai_suggestion",
        summary="仅作为合成建议",
        step_instance_id=None,
    )
    assert affair["decisions"][0]["can_drive_high_impact_branch"] is False

    affair = _complete(service, token, affair, "intake")
    affair = _complete(service, token, affair, "communication")
    affair = _complete(service, token, affair, "safety_handoff")
    affair = _complete(service, token, affair, "finish")
    affair = service.sop.close_affair(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="close-before-reopen",
        revision=int(affair["revision"]),
        closure_summary="合成结案",
    )
    reopened = service.sop.reopen_affair(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="reopen-sop-affair",
        revision=int(affair["revision"]),
        reason="出现新的合成信息",
    )

    assert reopened["state"] == "active"
    assert reopened["occurrence_sequence"] == 2
    assert [item["key"] for item in reopened["current_steps"]] == ["intake"]
