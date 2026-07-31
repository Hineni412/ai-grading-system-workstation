from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成SOP基线密码-足够长-001"


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
        operation_id="initialize-sop-baselines",
    )
    return service, str(initialized["session_token"])


def _template(
    baselines: dict[str, object],
    key: str,
) -> dict[str, object]:
    return next(
        item for item in baselines["items"]
        if item["template_key"] == key
    )


def _create(
    service: VaultService,
    token: str,
    template: dict[str, object],
    operation_id: str,
) -> dict[str, object]:
    return service.sop.create_affair(
        token=token,
        operation_id=operation_id,
        template_version_id=str(template["template_version_id"]),
        title=f"{template['title']}合成实例",
        summary="只用于 B06 合成检查",
        participant_refs=["synthetic-subject-a"],
    )


def _step(affair: dict[str, object], key: str) -> dict[str, object]:
    return next(
        item
        for item in [
            *affair["current_steps"],
            *affair["completed_steps"],
            *affair["preview_steps"],
        ]
        if item["key"] == key
    )


def _complete(
    service: VaultService,
    token: str,
    affair: dict[str, object],
    key: str,
) -> dict[str, object]:
    step = _step(affair, key)
    return service.sop.complete_step(
        token=token,
        affair_id=str(affair["affair_id"]),
        step_instance_id=str(step["step_instance_id"]),
        operation_id=f"complete-baseline-{affair['affair_id']}-{key}",
        revision=int(step["revision"]),
        outcome="completed",
        result=f"{key} 合成人工结果",
    )


def test_six_baselines_are_idempotent_personal_checklists_without_model(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    first = service.sop_baselines.ensure_baselines(token=token)
    second = service.sop_baselines.ensure_baselines(token=token)

    assert len(first["items"]) == 6
    assert {
        item["template_version_id"] for item in first["items"]
    } == {
        item["template_version_id"] for item in second["items"]
    }
    assert all(
        item["workflow_scope"] == "personal_checklist"
        for item in first["items"]
    )
    assert all(item["school_config_gaps"] for item in first["items"])
    assert first["model_enabled"] is False
    assert first["physical_request_count"] == 0


def test_injury_affair_shows_rescue_and_reports_before_any_form(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    baselines = service.sop_baselines.ensure_baselines(token=token)
    injury = _template(baselines, "baseline.student_injury")
    affair = _create(service, token, injury, "create-injury-affair")

    assert affair["risk_level"] == "emergency"
    assert "立即救护" in str(affair["emergency_prompt"])
    assert {
        item["key"] for item in affair["current_steps"]
    } == {"first_aid", "school_emergency_report", "guardian_contact"}
    assert affair["workflow_scope"] == "personal_checklist"


def test_teacher_decision_routes_conflict_but_ai_suggestion_cannot(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    baselines = service.sop_baselines.ensure_baselines(token=token)
    conflict = _template(baselines, "baseline.student_conflict")
    affair = _create(service, token, conflict, "create-conflict-affair")
    for key in ["safety_check", "separate_statements", "fact_check", "route"]:
        affair = _complete(service, token, affair, key)
    assert affair["current_steps"] == []

    affair = service.sop.record_decision(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="ai-cannot-route-conflict",
        decision_kind="ai_suggestion",
        summary="仅建议核查重复围堵信息",
        step_instance_id=None,
        decision_key="conflict_route",
        selected_option="suspected_bullying",
    )
    assert affair["current_steps"] == []

    affair = service.sop.record_decision(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="teacher-routes-conflict",
        decision_kind="teacher",
        summary="教师确认存在重复围堵信息，转学校核查",
        step_instance_id=None,
        decision_key="conflict_route",
        selected_option="suspected_bullying",
    )
    assert [item["key"] for item in affair["current_steps"]] == [
        "bullying_handoff"
    ]
    assert {
        item["key"] for item in affair["completed_steps"]
        if item["state"] == "superseded"
    } == {"ordinary_support", "emergency_handoff"}


def test_family_draft_excludes_other_student_identity_and_is_unsent(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    baselines = service.sop_baselines.ensure_baselines(token=token)
    family = _template(baselines, "baseline.family_communication")
    affair = _create(service, token, family, "create-family-affair")
    affair = _complete(service, token, affair, "purpose")
    affair = _complete(service, token, affair, "fact_selection")
    outline = _step(affair, "outline")
    draft = outline["communication_templates"][0]

    assert draft["status"] == "unsent"
    assert draft["audience"] == "当前学生家长"
    assert "其他学生身份" not in draft["content"]
    assert "另一学生" not in draft["content"]


def test_activity_template_activates_parallel_assignment_materials_and_safety(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    baselines = service.sop_baselines.ensure_baselines(token=token)
    activity = _template(baselines, "baseline.school_activity")
    affair = _create(service, token, activity, "create-activity-affair")
    affair = _complete(service, token, affair, "scope")

    assert {
        item["key"] for item in affair["current_steps"]
    } == {"assignment", "materials", "safety_plan"}
    assert {
        item["key"] for item in affair["preview_steps"]
    } == {"final_check", "activity_result", "retrospective"}
