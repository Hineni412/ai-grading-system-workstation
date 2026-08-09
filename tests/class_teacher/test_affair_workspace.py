from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_parallel_steps_share_one_projection_and_draft_is_plaintext(tmp_path: Path) -> None:
    service = VaultService(WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(project_root=PROJECT_ROOT, migration_project_root=PROJECT_ROOT),
    ))
    service.ensure_plaintext_ready()
    token = ""
    template = service.sop.publish_template(
        token=token,
        operation_id="affair-workspace-template",
        template_key="parallel-synthetic",
        version=1,
        title="合成并行事务",
        steps=[
            {"key": "start", "title": "开始", "details": None, "required": True, "waivable": False, "safety_required": False, "depends_on": []},
            {"key": "a", "title": "并行甲", "details": None, "required": True, "waivable": False, "safety_required": False, "depends_on": ["start"]},
            {"key": "b", "title": "并行乙", "details": None, "required": True, "waivable": False, "safety_required": False, "depends_on": ["start"]},
            {"key": "c", "title": "并行丙", "details": None, "required": True, "waivable": False, "safety_required": False, "depends_on": ["start"]},
        ],
    )
    affair = service.sop.create_affair(
        token=token,
        operation_id="affair-workspace-create",
        template_version_id=str(template["template_version_id"]),
        title="合成事务",
        summary="没有真实业务内容",
        participant_refs=["synthetic-participant"],
    )
    start = affair["current_steps"][0]
    affair = service.affairs.advance(
        token=token,
        affair_id=str(affair["affair_id"]),
        command="complete_step",
        operation_id="affair-workspace-advance",
        expected_revision=int(start["revision"]),
        step_instance_id=str(start["step_instance_id"]),
        outcome="completed",
        result="合成完成",
    )
    assert len(affair["current_steps"]) == 3
    draft = service.affairs.save_draft(
        token=token,
        affair_id=str(affair["affair_id"]),
        step_instance_id=str(affair["current_steps"][0]["step_instance_id"]),
        draft_kind="fact",
        text="仅保存在班主任工作台中的合成草稿",
        expected_revision=None,
        operation_id="affair-workspace-draft",
    )
    assert draft["revision"] == 1
    with closing(service.database.connect()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sensitive_work_groups WHERE source_kind = 'sensitive_affair' AND source_id = ?",
            (affair["affair_id"],),
        ).fetchone()[0] == 1
    assert "仅保存在班主任工作台中的合成草稿".encode("utf-8") in service.database.database_path.read_bytes()


def _decision_affair(tmp_path: Path) -> tuple[VaultService, str, dict[str, object]]:
    service = VaultService(WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(project_root=PROJECT_ROOT, migration_project_root=PROJECT_ROOT),
    ))
    service.ensure_plaintext_ready()
    token = ""
    template = service.sop.publish_template(
        token=token,
        operation_id="affair-decision-template",
        template_key="decision-synthetic",
        version=1,
        title="合成决定事务",
        steps=[{
            "key": "route",
            "title": "选择人工路径",
            "details": None,
            "required": True,
            "waivable": False,
            "safety_required": False,
            "depends_on": [],
            "decision_key": "manual_route",
            "decision_prompt": "由教师选择哪条人工路径？",
            "decision_options": [
                {"value": "observe", "label": "继续观察"},
                {"value": "follow_up", "label": "人工跟进"},
            ],
        }],
    )
    created = service.sop.create_affair(
        token=token,
        operation_id="affair-decision-create",
        template_version_id=str(template["template_version_id"]),
        title="合成决定事务",
        summary="只用于并发和步骤约束测试",
        participant_refs=["synthetic-participant"],
    )
    return service, token, service.affairs.read(
        token=token,
        affair_id=str(created["affair_id"]),
    )


def test_teacher_decision_requires_active_decision_step_and_current_workspace_revision(
    tmp_path: Path,
) -> None:
    service, token, affair = _decision_affair(tmp_path)
    step = affair["current_steps"][0]
    saved = service.affairs.advance(
        token=token,
        affair_id=str(affair["affair_id"]),
        command="teacher_decision",
        operation_id="affair-decision-save",
        expected_revision=int(affair["revision"]),
        step_instance_id=str(step["step_instance_id"]),
        decision_kind="teacher",
        summary="教师选择继续观察。",
        decision_key="manual_route",
        selected_option="observe",
    )
    assert saved["decisions"][0]["step_instance_id"] == step["step_instance_id"]

    with pytest.raises(VaultError) as stale:
        service.affairs.advance(
            token=token,
            affair_id=str(affair["affair_id"]),
            command="teacher_decision",
            operation_id="affair-decision-stale",
            expected_revision=int(affair["revision"]),
            step_instance_id=str(step["step_instance_id"]),
            decision_kind="teacher",
            summary="旧页面不应继续写入。",
            decision_key="manual_route",
            selected_option="follow_up",
        )
    assert stale.value.code == "sop_affair_revision_conflict"


def test_concurrent_teacher_decisions_check_revision_inside_the_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token, affair = _decision_affair(tmp_path)
    step = affair["current_steps"][0]
    original = service.sop.record_decision
    both_callers_passed_workspace_precheck = Barrier(2)

    def synchronized_record_decision(**kwargs: object) -> dict[str, object]:
        both_callers_passed_workspace_precheck.wait(timeout=5)
        return original(**kwargs)

    monkeypatch.setattr(service.sop, "record_decision", synchronized_record_decision)

    def submit(operation_id: str, option: str) -> tuple[str, str]:
        try:
            service.affairs.advance(
                token=token,
                affair_id=str(affair["affair_id"]),
                command="teacher_decision",
                operation_id=operation_id,
                expected_revision=int(affair["revision"]),
                step_instance_id=str(step["step_instance_id"]),
                decision_kind="teacher",
                summary=f"教师选择 {option}。",
                decision_key="manual_route",
                selected_option=option,
            )
            return ("saved", operation_id)
        except VaultError as error:
            return (error.code, operation_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda item: submit(*item),
            (("affair-decision-concurrent-a", "observe"),
             ("affair-decision-concurrent-b", "follow_up")),
        ))

    assert sorted(item[0] for item in results) == [
        "saved",
        "sop_affair_revision_conflict",
    ]
    saved = service.affairs.read(token=token, affair_id=str(affair["affair_id"]))
    assert len(saved["decisions"]) == 1


def test_affair_decision_and_projection_outbox_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token, affair = _decision_affair(tmp_path)
    step = affair["current_steps"][0]

    def fail_projection(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic outbox failure")

    monkeypatch.setattr(service.projections, "enqueue", fail_projection)
    with pytest.raises(RuntimeError, match="outbox failure"):
        service.affairs.advance(
            token=token,
            affair_id=str(affair["affair_id"]),
            command="teacher_decision",
            operation_id="affair-decision-rollback",
            expected_revision=int(affair["revision"]),
            step_instance_id=str(step["step_instance_id"]),
            decision_kind="teacher",
            summary="这条决定必须随 outbox 一起回滚。",
            decision_key="manual_route",
            selected_option="observe",
        )
    unchanged = service.affairs.read(token=token, affair_id=str(affair["affair_id"]))
    assert unchanged["revision"] == affair["revision"]
    assert unchanged["decisions"] == []


def test_affair_workspace_api_filters_internal_projection_mapping(tmp_path: Path) -> None:
    service, token, affair = _decision_affair(tmp_path)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    response = TestClient(app).get(
        f"/api/class-teacher/sop/affairs/{affair['affair_id']}",
        headers={"x-class-teacher-session": token},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["projection_state"] == "pending"
    assert "projection" not in payload
    assert "group_id" not in response.text
    assert "source_id" not in response.text
