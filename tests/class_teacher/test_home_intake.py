from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.home_intake import interpret_local_date
from backend.class_teacher.protection import FakeCurrentUserProtection
from backend.class_teacher.vault_service import VaultService
from backend.class_teacher.work_planning import FakeWorkPlanningGateway
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成首页入口保险箱密码-足够长-001"


def _plan(due_date: str | None = "2026-08-07") -> dict[str, object]:
    return {
        "kind": "plan",
        "questions": [],
        "assumptions": [],
        "nodes": [
            {
                "id": "goal",
                "kind": "goal",
                "title": "完成教师确认的普通任务",
                "details": None,
                "status": "pending",
                "due_date": due_date,
            }
        ],
        "edges": [],
    }


def _service(
    tmp_path: Path,
    *,
    result: dict[str, object] | str | None = None,
    available: bool = True,
    unknown: bool = False,
    request_count: int = 1,
    gateway: FakeWorkPlanningGateway | None = None,
) -> tuple[VaultService, str, FakeWorkPlanningGateway]:
    gateway = gateway or FakeWorkPlanningGateway(
        result=_plan() if result is None else result,
        available=available,
        unknown=unknown,
        request_count=request_count,
    )
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )
    service = VaultService(
        context,
        protection_provider=FakeCurrentUserProtection(b"H" * 32),
        model_gateway=gateway,
    )
    initialized = service.initialize(password=PASSWORD, operation_id="home-init-0001")
    return service, str(initialized["session_token"]), gateway


@pytest.mark.parametrize(
    ("text", "expected", "source"),
    (
        ("2026-08-09完成", "2026-08-09", "explicit_numeric"),
        ("8月9日完成", "2026-08-09", "explicit_numeric"),
        ("今天完成", "2026-08-03", "relative_day"),
        ("明天完成", "2026-08-04", "relative_day"),
        ("后天完成", "2026-08-05", "relative_day"),
        ("本周五完成", "2026-08-07", "relative_weekday"),
        ("这周周五完成", "2026-08-07", "relative_weekday"),
        ("下周一完成", "2026-08-10", "relative_weekday"),
    ),
)
def test_local_date_forms(text: str, expected: str, source: str) -> None:
    result = interpret_local_date(text, reference_date="2026-08-03")
    assert result["status"] == "resolved"
    assert result["resolved_date"] == expected
    assert result["source"] == source


def test_local_date_conflict_and_pending_metadata() -> None:
    conflict = interpret_local_date(
        "明天完成",
        selected_date="2026-08-05",
        reference_date="2026-08-03",
    )
    pending = interpret_local_date("本周完成", reference_date="2026-08-03")

    assert conflict["status"] == "conflict"
    assert conflict["candidates"] == ["2026-08-04", "2026-08-05"]
    assert pending == {
        "status": "pending",
        "source": "incomplete_week",
        "resolved_date": None,
        "selected_date": None,
        "candidates": [],
        "pending_reason": "已识别周范围，但仍需明确星期几",
    }


def test_named_fight_is_anonymized_exactly_before_any_request(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "先核对现场事实"},
    )

    preview = service.home_intake.prepare(
        token=token,
        text="王小明和张伟打架，明天核对现场事实",
        reference_date="2026-08-03",
    )

    assert preview["route"] == "sensitive"
    assert preview["recommended_route"] == "affair"
    assert preview["student_aliases"] == ["学生A", "学生B"]
    assert preview["exact_payload"]["task_text"] == "学生A和学生B打架，明天核对现场事实"
    assert preview["exact_payload"]["student_aliases"] == ["学生A", "学生B"]
    assert "王小明" not in json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert "张伟" not in json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert preview["physical_request_count"] == 0
    assert gateway.calls == []


def test_named_conflict_with_classroom_context_is_anonymized_before_any_request(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "先核对已确认事实"},
    )

    preview = service.home_intake.prepare(
        token=token,
        text="钱肖白和张立璞今天上信息课又发生了矛盾",
        reference_date="2026-08-03",
    )

    payload = json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert preview["route"] == "sensitive"
    assert preview["recommended_route"] == "affair"
    assert preview["student_aliases"] == ["学生A", "学生B"]
    assert preview["exact_payload"]["task_text"] == (
        "学生A和学生B今天上信息课又发生了矛盾"
    )
    assert "钱肖白" not in payload
    assert "张立璞" not in payload
    assert preview["physical_request_count"] == 0
    assert gateway.calls == []


def test_two_character_incident_names_keep_context_and_stable_aliases(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={
            "kind": "follow_up",
            "questions": ["双方目前是否仍在接触？"],
        },
    )
    preview = service.home_intake.prepare(
        token=token,
        text="王明和张伟今天上信息课又发生了矛盾",
        reference_date="2026-08-03",
    )

    assert preview["exact_payload"]["task_text"] == (
        "学生A和学生B今天上信息课又发生了矛盾"
    )
    assert preview["student_aliases"] == ["学生A", "学生B"]
    first = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-two-character-follow-up-001",
    )
    assert first["result_kind"] == "affair_recommendation"
    assert first["follow_up_questions"][0] == "双方目前是否仍在接触？"

    second_preview = service.home_intake.prepare_follow_up(
        token=token,
        operation_id="home-two-character-follow-up-001",
        answer="张伟仍在现场，王明已经离开",
        reference_date="2026-08-03",
        selected_step_keys=[str(first["result"]["steps"][0]["key"])],
    )
    second_payload = json.dumps(second_preview["exact_payload"], ensure_ascii=False)
    assert "王明" not in second_payload
    assert "张伟" not in second_payload
    assert "学生B仍在现场，学生A已经离开" in second_payload
    assert second_preview["student_aliases"] == ["学生A", "学生B"]
    assert len(gateway.calls) == 1


def test_named_incident_detection_has_no_short_context_cutoff(tmp_path: Path) -> None:
    service, token, gateway = _service(tmp_path)
    preview = service.home_intake.prepare(
        token=token,
        text=(
            "钱肖白和张立璞今天上午在学校计算机教室上信息技术课程"
            "并完成小组合作练习时发生了矛盾"
        ),
        reference_date="2026-08-03",
    )

    payload = json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert preview["route"] == "sensitive"
    assert preview["recommended_route"] == "affair"
    assert preview["student_aliases"] == ["学生A", "学生B"]
    assert "钱肖白" not in payload
    assert "张立璞" not in payload
    assert gateway.calls == []


@pytest.mark.parametrize(
    "incident",
    (
        "出现了冲突",
        "产生了冲突",
        "发生了争执",
        "出现了争执",
        "产生了争执",
    ),
)
def test_named_dispute_variants_recommend_affair(
    tmp_path: Path,
    incident: str,
) -> None:
    service, token, gateway = _service(tmp_path)
    preview = service.home_intake.prepare(
        token=token,
        text=f"王小明和张立璞{incident}",
        reference_date="2026-08-03",
    )

    assert preview["route"] == "sensitive"
    assert preview["recommended_route"] == "affair"
    assert preview["student_aliases"] == ["学生A", "学生B"]
    assert gateway.calls == []


def test_prevention_theme_stays_ordinary(tmp_path: Path) -> None:
    service, token, gateway = _service(tmp_path)

    preview = service.home_intake.prepare(
        token=token,
        text="本周五开展预防校园欺凌主题班会",
        reference_date="2026-08-03",
    )

    assert preview["route"] == "ordinary"
    assert preview["recommended_route"] == "ordinary_plan"
    second = service.home_intake.prepare(
        token=token,
        text="协调家长会和防欺凌主题班会安排",
        reference_date="2026-08-03",
    )
    third = service.home_intake.prepare(
        token=token,
        text="这是预防校园欺凌主题班会安排",
        reference_date="2026-08-03",
    )
    assert second["route"] == "ordinary"
    assert third["route"] == "ordinary"
    assert gateway.calls == []


def test_sensitive_preview_removes_unneeded_contact_details(tmp_path: Path) -> None:
    service, token, gateway = _service(tmp_path)

    preview = service.home_intake.prepare(
        token=token,
        text="王小明情绪低落，家长电话13800138000，家庭住址：合成路8号",
        reference_date="2026-08-03",
    )

    payload = json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert preview["dispatch_ready"] is True
    assert preview["blocked_categories"] == []
    assert {"手机号", "家庭住址"}.issubset(set(preview["removed_categories"]))
    assert "13800138000" not in payload
    assert "合成路8号" not in payload
    assert gateway.calls == []


@pytest.mark.parametrize(
    "contact",
    (
        "联系电话 138-0013-8000",
        "妈妈手机：+86 138 0013 8000",
        "家长电话0755-12345678",
        "父亲联系方式：138 0013 8000",
    ),
)
def test_sensitive_preview_removes_formatted_contact_values(
    tmp_path: Path,
    contact: str,
) -> None:
    service, token, gateway = _service(tmp_path)

    preview = service.home_intake.prepare(
        token=token,
        text=f"王小明情绪低落，{contact}",
        reference_date="2026-08-03",
    )

    payload = json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert preview["dispatch_ready"] is True
    assert not any(value in payload for value in ("138", "0013", "8000", "0755", "12345678"))
    assert gateway.calls == []


@pytest.mark.parametrize(
    "address_text",
    (
        "张三最近情绪低落，家住合成市晨光路8号",
        "张三最近情绪低落，住在合成市晨光路8号",
    ),
)
def test_sensitive_preview_minimizes_natural_address_forms(
    tmp_path: Path,
    address_text: str,
) -> None:
    service, token, gateway = _service(tmp_path)

    preview = service.home_intake.prepare(
        token=token,
        text=address_text,
        reference_date="2026-08-03",
    )

    payload = json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert preview["dispatch_ready"] is True
    assert "家庭住址" in preview["removed_categories"]
    assert "合成市晨光路8号" not in payload
    assert "张三" not in payload
    assert gateway.calls == []


def test_uncertain_labelled_parent_phone_fails_closed(tmp_path: Path) -> None:
    service, token, gateway = _service(tmp_path)

    preview = service.home_intake.prepare(
        token=token,
        text="王小明情绪低落，家长电话：稍后补充",
        reference_date="2026-08-03",
    )

    assert preview["dispatch_ready"] is False
    assert preview["local_only"] is True
    assert "家长电话" in preview["blocked_categories"]
    assert preview["exact_payload"] is None
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("text", "class_label", "student_name"),
    (
        ("八年级3班王小明最近情绪低落", "八年级3班", "王小明"),
        ("三年级二班的张三最近情绪低落", "三年级二班", "张三"),
        ("三年级二班学生张三最近情绪低落", "三年级二班", "张三"),
    ),
)
def test_class_and_name_are_both_removed_from_sensitive_preview(
    tmp_path: Path,
    text: str,
    class_label: str,
    student_name: str,
) -> None:
    service, token, gateway = _service(tmp_path)

    preview = service.home_intake.prepare(
        token=token,
        text=text,
        reference_date="2026-08-03",
    )

    payload = json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert preview["route"] == "sensitive"
    assert class_label not in payload
    assert student_name not in payload
    assert "学生A最近情绪低落" in payload
    assert "班级" in preview["removed_categories"]
    assert gateway.calls == []


def test_emergency_guidance_is_local_and_precedes_ai(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "按学校流程继续"},
    )

    preview = service.home_intake.prepare(
        token=token,
        text="王小明和张伟正在打架，有人受伤",
        reference_date="2026-08-03",
    )

    assert preview["route"] == "emergency"
    assert preview["emergency_guidance"]["priority"] == "before_ai"
    assert "不要等待 AI" in preview["emergency_guidance"]["title"]
    assert any("110 或 120" in step for step in preview["emergency_guidance"]["steps"])
    assert gateway.calls == []


def test_empty_kind_question_only_result_becomes_confirmable_first_draft(
    tmp_path: Path,
) -> None:
    question = (
        "你希望围绕9月1日开学完成哪类成果？"
        "例如报到流程、物资准备、家长通知或开学班会。"
    )
    service, token, gateway = _service(
        tmp_path,
        result={
            "kind": "",
            "questions": [question],
            "assumptions": [],
            "nodes": [],
            "edges": [],
            "summary": "",
            "reasons": [],
        },
    )
    preview = service.home_intake.prepare(
        token=token,
        text="9月1日学生开学",
        reference_date="2026-08-03",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-empty-kind-follow-up-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "ordinary_plan"
    assert result["follow_up_questions"] == [question]
    assert result["teacher_confirmation_required"] is True
    assert len(result["result"]["nodes"]) == 3
    assert result["physical_request_count"] == 1
    assert len(gateway.calls) == 1
    payload = gateway.calls[0]["payload"]
    assert any("kind 是必填字段" in item for item in payload["instructions"])


def test_empty_kind_mixed_plan_and_questions_remains_invalid(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={
            "kind": "",
            "questions": ["希望形成哪类成果？"],
            "assumptions": [],
            "nodes": _plan()["nodes"],
            "edges": [],
        },
    )
    preview = service.home_intake.prepare(
        token=token,
        text="安排开学工作",
        reference_date="2026-08-03",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-empty-kind-mixed-001",
    )

    assert result["state"] == "invalid_result"
    assert result["result_kind"] is None
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    "model_result",
    (
        {
            "kind": False,
            "questions": ["希望形成哪类成果？"],
            "nodes": [],
            "edges": [],
        },
        {
            "kind": "",
            "questions": [123],
            "nodes": [],
            "edges": [],
        },
        {
            "kind": "follow_up",
            "questions": ["希望形成哪类成果？"],
            "nodes": [],
            "edges": [],
            "summary": "同时建议转入事务",
        },
        {
            "kind": "follow_up",
            "questions": ["问题一", "问题二", "问题三", "问题四"],
            "nodes": [],
            "edges": [],
        },
    ),
)
def test_malformed_or_mixed_follow_up_results_remain_invalid(
    tmp_path: Path,
    model_result: dict[str, object],
) -> None:
    service, token, gateway = _service(tmp_path, result=model_result)
    preview = service.home_intake.prepare(
        token=token,
        text="安排开学工作",
        reference_date="2026-08-03",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id=f"home-invalid-follow-up-{abs(hash(json.dumps(model_result, default=str)))}",
    )

    assert result["state"] == "invalid_result"
    assert result["result_kind"] is None
    assert len(gateway.calls) == 1


def test_markdown_fenced_plan_needs_no_repair_request(tmp_path: Path) -> None:
    fenced = f"```json\n{json.dumps(_plan(), ensure_ascii=False)}\n```"
    service, token, gateway = _service(tmp_path, result=fenced)
    preview = service.home_intake.prepare(
        token=token,
        text="本周五完成班级材料整理",
        reference_date="2026-08-03",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-fenced-plan-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "ordinary_plan"
    assert result["teacher_confirmation_required"] is True
    assert len(gateway.calls) == 1
    assert service.work.query(as_of="2026-08-07")["nodes"] == []


def test_ordinary_intake_does_not_require_sensitive_vault_session(tmp_path: Path) -> None:
    service, _token, gateway = _service(tmp_path)
    preview = service.home_intake.prepare(
        token="",
        text="本周五完成班级材料整理",
        reference_date="2026-08-03",
    )

    dispatched = service.home_intake.dispatch(
        token="",
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-ordinary-locked-001",
    )
    queried = service.home_intake.status(
        token="",
        operation_id="home-ordinary-locked-001",
    )

    assert dispatched["state"] == "succeeded"
    assert queried["state"] == "succeeded"
    assert len(gateway.calls) == 1


def test_ordinary_ai_can_recommend_a_handoff_without_persisting(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={
            "kind": "affair_recommendation",
            "summary": "这项工作需要连续核对多个步骤，建议进入事务流程。",
            "reasons": ["需要保留连续处理记录"],
            "questions": [],
            "assumptions": [],
        },
    )
    preview = service.home_intake.prepare(
        token=token,
        text="持续跟进家长会回执收集与补交",
        reference_date="2026-08-03",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-ordinary-affair-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "affair_recommendation"
    assert result["result"]["summary"].startswith("这项工作")
    assert result["teacher_confirmation_required"] is True
    assert service.work.query(as_of="2026-08-03")["nodes"] == []
    assert len(gateway.calls) == 1


def test_ordinary_safe_natural_language_reply_can_continue(tmp_path: Path) -> None:
    service, token, _gateway = _service(
        tmp_path,
        result="请补充活动对象和具体要求，再由教师决定是否继续整理。",
    )
    preview = service.home_intake.prepare(
        token=token,
        text="准备一次班级活动",
        reference_date="2026-08-03",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-ordinary-text-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "plain_text"
    assert result["can_follow_up"] is True
    assert "请补充活动对象" in result["result"]["text"]
    calls_before = len(_gateway.calls)
    next_preview = service.home_intake.prepare_follow_up(
        token=token,
        operation_id="home-ordinary-text-001",
        answer="对象是全班学生，时间是下周五",
        reference_date="2026-08-03",
    )
    assert next_preview["round_number"] == 2
    assert len(_gateway.calls) == calls_before
    assert service.work.query(as_of="2026-08-03")["nodes"] == []


def _dispatch_sensitive(
    service: VaultService,
    token: str,
    *,
    text: str,
    operation_id: str,
) -> dict[str, object]:
    preview = service.home_intake.prepare(
        token=token,
        text=text,
        reference_date="2026-08-03",
    )
    return service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id=operation_id,
    )


def test_safe_plain_text_is_wrapped_in_a_useful_sensitive_draft(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result="先记录可核对事实并联系校内负责人，再由教师选择后续路径。",
    )

    result = _dispatch_sensitive(
        service,
        token,
        text="王小明最近情绪低落，希望梳理下一步",
        operation_id="home-plain-text-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "student_support_recommendation"
    assert len(result["result"]["steps"]) >= 5
    assert len(gateway.calls) == 1


def test_normal_conflict_recommendation_is_not_mistaken_for_a_student_name(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={
            "assumptions": ["暂按普通学生矛盾处理"],
            "calendar_items": [
                "calendar.confirm_safety",
                "calendar.record_conflict",
                "calendar.verify_details",
            ],
            "kind": "affair_recommendation",
            "steps": ["confirm_safety", "record_conflict", "verify_details"],
            "summary": "先确保安全，再分别记录、核实并跟进。",
            "template_key": "baseline.student_conflict",
            "title": "学生矛盾处理初稿",
            "to_verify": [
                "是否有人受伤或仍存在即时风险",
                "矛盾的具体起因",
                "当前双方的状态是否稳定",
            ],
        },
    )

    result = _dispatch_sensitive(
        service,
        token,
        text="钱肖白和张立璞今天信息课又发生了矛盾",
        operation_id="home-normal-conflict-output-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "affair_recommendation"
    assert result["result"]["template_key"] == "baseline.student_conflict"
    assert len(result["result"]["steps"]) >= 5
    assert len(result["result"]["calendar_items"]) == len(result["result"]["steps"])
    scheduled_dates = [item["due_date"] for item in result["result"]["calendar_items"]]
    assert len(set(scheduled_dates)) < len(scheduled_dates)
    assert scheduled_dates[:3] == [scheduled_dates[0]] * 3
    assert len(gateway.calls) == 1


def test_opening_day_plan_keeps_ai_guidance_and_turns_questions_into_verification(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={
            "kind": "plan",
            "assumptions": [],
            "questions": [
                "本次开学是否有明确的核心任务或成果目标？",
                "是否存在与本次开学相关的前置已完成工作？",
            ],
            "nodes": [
                {
                    "id": "1",
                    "kind": "goal",
                    "title": "完成九月一日开学准备",
                    "details": "确保开学首日各项工作顺利开展",
                    "rationale": "把日期事件转为可执行准备方案",
                    "status": "pending",
                    "due_date": "202X-09-01",
                },
                {
                    "id": "2",
                    "kind": "task",
                    "title": "开学物资筹备",
                    "details": "筹备开学首日所需各项物资与材料",
                    "rationale": "提前完成物资筹备以保障开学首日使用",
                    "status": "pending",
                    "due_date": "202X-08-25",
                },
            ],
            "edges": [
                {"source_id": "1", "target_id": "2", "relation": "contains"}
            ],
        },
    )
    preview = service.home_intake.prepare(
        token=token,
        text="九月一日开学",
        reference_date="2026-08-04",
    )
    assert preview["final_due_date"] == "2026-09-01"

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-opening-day-guidance-001",
    )

    assert result.get("validation_issue") is None, result.get("validation_issue")
    assert result["state"] == "succeeded"
    assert result["result_kind"] == "ordinary_plan"
    assert [node["title"] for node in result["result"]["nodes"]] == [
        "完成九月一日开学准备",
        "开学物资筹备",
    ]
    assert result["result"]["nodes"][1]["due_date"] == "2026-08-25"
    assert [node["draft_key"] for node in result["result"]["nodes"]] == [
        "node_1",
        "node_2",
    ]
    assert result["result"]["edges"][0]["source_draft_key"] == "node_1"
    assert result["result"]["edges"][0]["target_draft_key"] == "node_2"
    assert any("核心任务" in item for item in result["result"]["assumptions"])
    assert len(gateway.calls) == 1


def test_model_judgement_is_visible_for_teacher_review(tmp_path: Path) -> None:
    advice = "已经认定属于欺凌，必须给予处分并自动结案。"
    service, token, gateway = _service(tmp_path, result=advice)

    result = _dispatch_sensitive(
        service,
        token,
        text="王小明和张伟发生冲突",
        operation_id="home-unsafe-output-001",
    )

    assert result["state"] == "succeeded"
    assert result["result"]["model_advice"] == advice
    assert result["teacher_confirmation_required"] is True
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    ("model_result", "operation_id"),
    (
        ("该生患有抑郁症", "home-unsafe-diagnosis-text"),
        ("该学生是重度焦虑症", "home-unsafe-diagnosis-equivalent"),
        ("这是校园欺凌行为", "home-unsafe-bullying-text"),
        ("该行为构成网络欺凌事件", "home-unsafe-bullying-equivalent"),
        ("建议停课一周", "home-unsafe-suspension-text"),
        ("停学两周", "home-unsafe-suspension-equivalent"),
        ("请联系家长13812345678", "home-unsafe-phone-output"),
        ("家庭住址为北京市海淀区合成路8号", "home-unsafe-address-output"),
        ("请查看该生病历后再安排", "home-unsafe-medical-record-output"),
        (
            {"kind": "student_support_recommendation", "summary": "该生患有抑郁症"},
            "home-unsafe-diagnosis-json",
        ),
        (
            {"kind": "student_support_recommendation", "reasons": ["这是校园欺凌行为"]},
            "home-unsafe-bullying-json",
        ),
        (
            {"kind": "student_support_recommendation", "assumptions": ["建议停课一周"]},
            "home-unsafe-suspension-json",
        ),
    ),
)
def test_explicit_diagnostic_and_high_impact_outputs_remain_visible(
    tmp_path: Path,
    model_result: dict[str, object] | str,
    operation_id: str,
) -> None:
    service, token, gateway = _service(tmp_path, result=model_result)

    result = _dispatch_sensitive(
        service,
        token,
        text="王小明最近情绪低落",
        operation_id=operation_id,
    )

    assert result["state"] == "succeeded"
    rendered = json.dumps(result["result"], ensure_ascii=False)
    expected_text = (
        str(model_result)
        if isinstance(model_result, str)
        else json.dumps(model_result, ensure_ascii=False)
    )
    for fragment in ("抑郁症", "焦虑症", "欺凌", "停课", "停学", "13812345678", "家庭住址", "病历", "处分"):
        if fragment in expected_text:
            assert fragment in rendered
    assert result["teacher_confirmation_required"] is True
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("text", ("九月十一一日开学", "九月十十日开学"))
def test_malformed_chinese_dates_are_rejected(tmp_path: Path, text: str) -> None:
    service, token, gateway = _service(tmp_path)

    with pytest.raises(VaultError) as invalid:
        service.home_intake.prepare(
            token=token,
            text=text,
            reference_date="2026-08-04",
        )

    assert invalid.value.code == "home_intake_date_invalid"
    assert len(gateway.calls) == 0


@pytest.mark.parametrize(
    ("text", "kind", "summary"),
    (
        ("王小明和张伟发生冲突", "affair_recommendation", "建议进入事务流程核对事实"),
        ("王小明最近情绪低落", "student_support_recommendation", "建议进入学生支持路径"),
    ),
)
def test_sensitive_route_recommendation_kinds(
    tmp_path: Path,
    text: str,
    kind: str,
    summary: str,
) -> None:
    service, token, _gateway = _service(tmp_path, result={"kind": kind, "summary": summary})

    result = _dispatch_sensitive(
        service,
        token,
        text=text,
        operation_id=f"home-route-{kind}",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == kind
    assert result["result"]["summary"] == summary


@pytest.mark.parametrize(
    "result",
    (
        {"kind": "student_support_recommendation", "summary": 42},
        {"kind": "student_support_recommendation", "reasons": "先核对"},
        {"kind": "student_support_recommendation", "reasons": [42]},
        {"kind": "student_support_recommendation", "assumptions": "无"},
        {"kind": "student_support_recommendation", "assumptions": [None]},
    ),
)
def test_malformed_sensitive_recommendation_shapes_are_invalid(
    tmp_path: Path,
    result: dict[str, object],
) -> None:
    service, token, _gateway = _service(tmp_path, result=result)

    dispatched = _dispatch_sensitive(
        service,
        token,
        text="王小明最近情绪低落",
        operation_id=f"home-malformed-result-{abs(hash(json.dumps(result, default=str)))}",
    )

    assert dispatched["state"] == "invalid_result"
    assert dispatched["result"] is None
    assert dispatched["teacher_confirmation_required"] is False


def test_sensitive_recommendation_is_normalized_server_side(tmp_path: Path) -> None:
    service, token, _gateway = _service(
        tmp_path,
        result={
            "kind": "student_support_recommendation",
            "summary": None,
            "reasons": [" 先核对 已知事实 "],
            "assumptions": [" 不补全 未知信息 "],
        },
    )

    dispatched = _dispatch_sensitive(
        service,
        token,
        text="王小明最近情绪低落",
        operation_id="home-normalized-result-001",
    )

    assert dispatched["state"] == "succeeded"
    assert dispatched["result"]["kind"] == "student_support_recommendation"
    assert dispatched["result"]["reasons"] == ["先核对 已知事实"]
    assert dispatched["result"]["assumptions"] == ["不补全 未知信息"]
    assert dispatched["result"]["template_key"] == "baseline.care_conversation"
    assert dispatched["result"]["steps"]


@pytest.mark.parametrize("result", ("", "ok", "abc12345", "。"))
def test_nonmeaningful_non_json_sensitive_results_are_invalid(
    tmp_path: Path,
    result: str,
) -> None:
    service, token, _gateway = _service(tmp_path, result=result)

    dispatched = _dispatch_sensitive(
        service,
        token,
        text="王小明最近情绪低落",
        operation_id=f"home-empty-text-{len(result)}-{ord(result[0]) if result else 0}",
    )

    assert dispatched["state"] == "invalid_result"
    assert dispatched["result_kind"] is None
    assert dispatched["error_category"] == "nonmeaningful_plain_text"


def test_sensitive_empty_kind_question_only_result_becomes_sop_draft(
    tmp_path: Path,
) -> None:
    question = "请补充已经确认的现场事实，例如双方是否仍在接触、是否有人受伤。"
    service, token, gateway = _service(
        tmp_path,
        result={
            "kind": "",
            "questions": [question],
            "assumptions": [],
            "nodes": [],
            "edges": [],
            "summary": "",
            "reasons": [],
        },
    )

    result = _dispatch_sensitive(
        service,
        token,
        text="王小明和张伟发生冲突",
        operation_id="home-sensitive-empty-kind-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "affair_recommendation"
    assert result["follow_up_questions"][0] == question
    assert result["teacher_confirmation_required"] is True
    assert result["result"]["template_key"] == "baseline.student_conflict"
    assert result["result"]["steps"]
    assert len(gateway.calls) == 1
    contract = gateway.calls[0]["payload"]["output_contract"]
    assert "follow_up" not in contract["allowed_kinds"]
    assert any("不得只追问" in rule for rule in contract["rules"])


@pytest.mark.parametrize(
    "model_result",
    (
        {
            "kind": False,
            "questions": ["双方目前是否仍在接触？"],
            "nodes": [],
            "edges": [],
        },
        {
            "kind": "follow_up",
            "questions": ["双方目前是否仍在接触？"],
            "nodes": [],
            "edges": [],
            "summary": "同时建议转入事务",
        },
        {
            "kind": "follow_up",
            "questions": ["问题一", "问题二", "问题三", "问题四"],
            "nodes": [],
            "edges": [],
        },
    ),
)
def test_sensitive_malformed_or_mixed_follow_up_remains_invalid(
    tmp_path: Path,
    model_result: dict[str, object],
) -> None:
    service, token, gateway = _service(tmp_path, result=model_result)
    result = _dispatch_sensitive(
        service,
        token,
        text="王小明和张立璞发生冲突",
        operation_id=(
            "home-sensitive-invalid-follow-up-"
            f"{abs(hash(json.dumps(model_result, default=str)))}"
        ),
    )

    assert result["state"] == "invalid_result"
    assert result["result_kind"] is None
    assert len(gateway.calls) == 1


def test_follow_up_starts_next_round_only_on_explicit_call(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "follow_up", "questions": ["两名学生现在是否已经分开？"]},
    )
    first = _dispatch_sensitive(
        service,
        token,
        text="王小明和张伟发生冲突",
        operation_id="home-follow-up-round-001",
    )

    assert first["result_kind"] == "affair_recommendation"
    assert first["round_number"] == 1
    assert first["cumulative_physical_request_count"] == 1
    assert len(gateway.calls) == 1
    service.home_intake.status(token=token, operation_id="home-follow-up-round-001")
    assert len(gateway.calls) == 1

    second_preview = service.home_intake.prepare_follow_up(
        token=token,
        operation_id="home-follow-up-round-001",
        answer="已经分开，目前没有继续接触",
        reference_date="2026-08-03",
        selected_step_keys=[str(first["result"]["steps"][0]["key"])],
    )
    assert second_preview["round_number"] == 2
    assert second_preview["student_aliases"] == ["学生A", "学生B"]
    assert "学生A和学生B发生冲突" in second_preview["exact_payload"]["task_text"]
    revision = second_preview["exact_payload"]["context"]["revision_context"]
    assert set(revision["selected_step_keys"]) == {
        item["key"] for item in first["result"]["steps"]
    }
    assert set(revision["selected_calendar_keys"]) == {
        item["key"] for item in first["result"]["calendar_items"]
    }
    assert "王小明" not in json.dumps(revision, ensure_ascii=False)
    assert second_preview["physical_request_count"] == 0
    assert second_preview["cumulative_physical_request_count"] == 1
    assert len(gateway.calls) == 1

    gateway.result = {
        "kind": "affair_recommendation",
        "summary": "继续核对事实并由教师选择学校事务流程",
    }
    second = service.home_intake.dispatch(
        token=token,
        preview_id=str(second_preview["preview_id"]),
        fingerprint=str(second_preview["fingerprint"]),
        operation_id="home-follow-up-round-002",
    )
    assert second["round_physical_request_count"] == 1
    assert second["cumulative_physical_request_count"] == 2
    assert len(gateway.calls) == 2


def test_revision_can_span_multiple_dates_without_being_blocked(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "先处理当前矛盾"},
    )
    first = _dispatch_sensitive(
        service,
        token,
        text="王小明和张伟今天发生冲突",
        operation_id="home-multi-date-revision-001",
    )

    preview = service.home_intake.prepare_follow_up(
        token=token,
        operation_id="home-multi-date-revision-001",
        answer="已确认无人受伤，请把后续安排压缩为今天和明天两天完成",
        reference_date="2026-08-03",
        selected_step_keys=[str(first["result"]["steps"][1]["key"])],
    )

    assert preview["date_interpretation"]["status"] == "conflict"
    assert preview["date_interpretation"]["candidates"] == [
        "2026-08-03",
        "2026-08-04",
    ]
    assert preview["dispatch_ready"] is True
    assert preview["exact_payload"] is not None
    assert preview["physical_request_count"] == 0
    assert len(gateway.calls) == 1

    gateway.result = {
        "kind": "affair_recommendation",
        "summary": "今天完成事实记录，明天完成后续跟进",
        "calendar_items": [
            {
                "key": "calendar.separate_statements",
                "step_key": "separate_statements",
                "title": "分别记录各方表述",
                "due_date": "2026-08-03",
                "depends_on": ["calendar.safety_check"],
            },
            {
                "key": "calendar.follow_up",
                "step_key": "follow_up",
                "title": "分别设置跟进并记录结果",
                "due_date": "2026-08-04",
                "depends_on": ["calendar.ordinary_support"],
            },
        ],
    }
    revised = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-multi-date-revision-002",
    )
    dates = {
        str(item["key"]): str(item["due_date"])
        for item in revised["result"]["calendar_items"]
    }
    assert revised["result"]["template_key"] == "baseline.student_conflict"
    assert dates["calendar.separate_statements"] == "2026-08-03"
    assert dates["calendar.follow_up"] == "2026-08-04"
    assert len(gateway.calls) == 2


def test_revision_with_new_injury_information_upgrades_the_sop_template(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "先处理当前矛盾"},
    )
    first = _dispatch_sensitive(
        service,
        token,
        text="王小明和张伟发生冲突",
        operation_id="home-injury-upgrade-revision-001",
    )
    assert first["result"]["template_key"] == "baseline.student_conflict"

    preview = service.home_intake.prepare_follow_up(
        token=token,
        operation_id="home-injury-upgrade-revision-001",
        answer="刚确认有学生骨折，正在急救",
        reference_date="2026-08-03",
        selected_step_keys=[str(first["result"]["steps"][0]["key"])],
    )
    gateway.result = {
        "kind": "affair_recommendation",
        "summary": "立即按学生受伤流程处理",
    }

    revised = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-injury-upgrade-revision-002",
    )

    assert revised["result"]["template_key"] == "baseline.student_injury"
    assert {step["key"] for step in revised["result"]["steps"]} != {
        step["key"] for step in first["result"]["steps"]
    }
    assert len(gateway.calls) == 2


def test_failed_revision_keeps_the_last_saved_draft_version(tmp_path: Path) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "先处理当前矛盾"},
    )
    first_operation = _dispatch_sensitive(
        service,
        token,
        text="王小明和张伟发生冲突",
        operation_id="home-durable-draft-round-001",
    )
    first = service.home_intake_drafts.capture(token=token, operation=first_operation)
    draft_id = str(first["draft_id"])
    assert first["draft_version"] == 1

    preview = service.home_intake.prepare_follow_up(
        token=token,
        operation_id="home-durable-draft-round-001",
        answer="前两步已经完成，后续安排需要调整",
        reference_date="2026-08-04",
        selected_step_keys=[str(first_operation["result"]["steps"][2]["key"])],
    )
    gateway.result = {"kind": "unsupported"}
    failed_operation = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-durable-draft-round-002",
    )
    failed = service.home_intake_drafts.capture(token=token, operation=failed_operation)

    assert failed["state"] == "invalid_result"
    assert failed["previous_result_preserved"] is True
    assert failed["draft_id"] == draft_id
    assert failed["draft_version"] == 1
    assert failed["preserved_result"] == first_operation["result"]
    restored = service.home_intake_drafts.get(token=token, draft_id=draft_id)
    assert restored["version"] == 1
    assert restored["operation"]["draft_id"] == draft_id
    assert restored["operation"]["draft_version"] == 1
    assert restored["operation"]["draft_saved_at"] == restored["updated_at"]
    assert restored["operation"]["result"] == first_operation["result"]
    assert restored["operation"]["local_context"]["source_text"] == "王小明和张伟发生冲突"
    summary = service.home_intake_drafts.list_open(token=token)["items"][0]
    assert summary["draft_id"] == draft_id
    assert summary["source_text"] == "王小明和张伟发生冲突"
    assert summary["student_aliases"] == ["学生A", "学生B"]


def test_adoption_claim_blocks_a_concurrent_sensitive_revision(tmp_path: Path) -> None:
    service, token, _gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "先处理当前矛盾"},
    )
    operation = _dispatch_sensitive(
        service,
        token,
        text="王小明和张伟发生冲突",
        operation_id="home-adoption-claim-001",
    )
    captured = service.home_intake_drafts.capture(token=token, operation=operation)
    service.home_intake_drafts.begin_adoption(
        token=token,
        draft_id=str(captured["draft_id"]),
        version=int(captured["draft_version"]),
        source_operation_id=str(operation["operation_id"]),
        result_fingerprint=str(operation["result_fingerprint"]),
    )
    concurrent = {
        **operation,
        "operation_id": "home-adoption-claim-002",
        "local_context": {
            **dict(operation["local_context"]),
            "prior_operations": [operation["operation_id"]],
        },
    }

    with pytest.raises(VaultError) as captured_error:
        service.home_intake_drafts.capture(token=token, operation=concurrent)

    assert captured_error.value.code == "home_intake_draft_closed"


def test_ordinary_draft_can_close_after_work_graph_confirmation(tmp_path: Path) -> None:
    service, token, _gateway = _service(tmp_path)
    preview = service.home_intake.prepare(
        token=token,
        text="九月一日开学，提前完成开学准备",
        reference_date="2026-08-04",
    )
    operation = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-ordinary-draft-001",
    )
    captured = service.home_intake_drafts.capture(token=token, operation=operation)

    closed = service.home_intake_finalizer.adopt_ordinary(
        token=token,
        draft_id=str(captured["draft_id"]),
        expected_version=int(captured["draft_version"]),
        source_operation_id=str(operation["operation_id"]),
        result_fingerprint=str(operation["result_fingerprint"]),
    )
    replay = service.home_intake_finalizer.adopt_ordinary(
        token=token,
        draft_id=str(captured["draft_id"]),
        expected_version=int(captured["draft_version"]),
        source_operation_id=str(operation["operation_id"]),
        result_fingerprint=str(operation["result_fingerprint"]),
    )

    assert closed["state"] == "complete"
    assert replay["operation_id"] == closed["operation_id"]
    assert service.home_intake_drafts.list_open(token=token)["items"] == []


def test_ordinary_draft_remains_available_while_sensitive_vault_is_locked(
    tmp_path: Path,
) -> None:
    service, token, _gateway = _service(tmp_path)
    service.lock(token)
    preview = service.home_intake.prepare(
        token="",
        text="九月一日开学，提前完成开学准备",
        reference_date="2026-08-04",
    )
    operation = service.home_intake.dispatch(
        token="",
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-ordinary-locked-001",
    )

    captured = service.home_intake_drafts.capture(token="", operation=operation)

    assert captured["draft_id"]
    assert service.home_intake_drafts.get(
        token="", draft_id=str(captured["draft_id"])
    )["result_kind"] == "ordinary_plan"
    assert service.home_intake_drafts.list_open(token="")["items"][0]["draft_id"] == captured["draft_id"]


def test_destination_change_inside_invoke_is_zero_request_state(tmp_path: Path) -> None:
    class _LateDestinationChangeGateway(FakeWorkPlanningGateway):
        expected_fingerprint: str | None = None

        def invoke(self, **kwargs: object) -> str:
            self.expected_fingerprint = str(
                kwargs.get("expected_destination_fingerprint") or ""
            )
            self.model = "changed-after-home-check"
            return super().invoke(**kwargs)

    gateway = _LateDestinationChangeGateway(
        result={"kind": "student_support_recommendation", "summary": None}
    )
    service, token, _gateway = _service(tmp_path, gateway=gateway)
    preview = service.home_intake.prepare(
        token=token,
        text="王小明最近情绪低落",
        reference_date="2026-08-03",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-late-destination-001",
    )

    assert result["state"] == "destination_changed"
    assert result["error_category"] == "destination_changed"
    assert result["physical_request_count"] == 0
    assert gateway.expected_fingerprint == preview["destination_fingerprint"]
    assert gateway.calls == []


def test_expired_sensitive_preview_has_durable_zero_request_state(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(tmp_path)
    preview = service.home_intake.prepare(
        token=token,
        text="王小明最近情绪低落",
        reference_date="2026-08-03",
    )
    with closing(service.ordinary_database.connect()) as connection:
        with connection:
            connection.execute(
                "UPDATE model_approval_operations SET expires_at = ? WHERE preview_id = ?",
                ("2000-01-01T00:00:00+00:00", preview["preview_id"]),
            )

    with pytest.raises(VaultError) as caught:
        service.home_intake.dispatch(
            token=token,
            preview_id=str(preview["preview_id"]),
            fingerprint=str(preview["fingerprint"]),
            operation_id="home-expired-preview-001",
        )
    recovered = service.home_intake.status(
        token=token,
        operation_id="home-expired-preview-001",
    )

    assert caught.value.code == "class_teacher_model_preview_expired"
    assert recovered["state"] == "failed_before_send"
    assert recovered["error_category"] == "class_teacher_model_preview_expired"
    assert recovered["physical_request_count"] == 0
    assert gateway.calls == []


def test_locked_session_failure_stays_authorized_and_zero_request(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(tmp_path)
    preview = service.home_intake.prepare(
        token=token,
        text="王小明最近情绪低落",
        reference_date="2026-08-03",
    )
    service.lock(token)

    with pytest.raises(VaultError) as dispatch_error:
        service.home_intake.dispatch(
            token=token,
            preview_id=str(preview["preview_id"]),
            fingerprint=str(preview["fingerprint"]),
            operation_id="home-locked-session-001",
        )
    with pytest.raises(VaultError) as status_error:
        service.home_intake.status(
            token=token,
            operation_id="home-locked-session-001",
        )

    new_token = str(service.unlock(password=PASSWORD)["session_token"])
    recovered = service.home_intake.status(
        token=new_token,
        operation_id="home-locked-session-001",
    )
    assert dispatch_error.value.code == "vault_locked"
    assert status_error.value.code == "vault_locked"
    assert recovered["state"] == "failed_before_send"
    assert recovered["error_category"] == "vault_locked"
    assert recovered["physical_request_count"] == 0
    assert gateway.calls == []


def test_query_only_unknown_recovery_never_resends(tmp_path: Path) -> None:
    service, token, gateway = _service(tmp_path, unknown=True)
    preview = service.home_intake.prepare(
        token=token,
        text="王小明最近情绪低落",
        reference_date="2026-08-03",
    )
    first = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-unknown-result-001",
    )
    queried = service.home_intake.status(token=token, operation_id="home-unknown-result-001")

    assert first["state"] == "result_unknown"
    assert queried == first
    assert len(gateway.calls) == 1


def test_physical_request_counts_include_gateway_attempts(tmp_path: Path) -> None:
    service, token, _gateway = _service(tmp_path, request_count=3)
    preview = service.home_intake.prepare(
        token=token,
        text="本周五完成班级材料整理",
        reference_date="2026-08-03",
    )
    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-request-count-001",
    )

    assert result["round_physical_request_count"] == 3
    assert result["cumulative_physical_request_count"] == 3


def test_manual_one_node_fallback_is_idempotent_and_model_free(tmp_path: Path) -> None:
    service, token, gateway = _service(tmp_path, available=False)
    preview = service.home_intake.prepare(
        token=token,
        text="本周五完成班级材料整理",
        reference_date="2026-08-03",
    )
    unavailable = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-manual-source-001",
    )
    calls_before = len(gateway.calls)

    first = service.home_intake.confirm_manual_fallback(
        token=token,
        source_operation_id="home-manual-source-001",
        operation_id="home-manual-save-001",
    )
    replay = service.home_intake.confirm_manual_fallback(
        token=token,
        source_operation_id="home-manual-source-001",
        operation_id="home-manual-save-001",
    )
    buggy_new_operation_replay = service.home_intake.confirm_manual_fallback(
        token=token,
        source_operation_id="home-manual-source-001",
        operation_id="home-manual-save-002",
        title="客户端错误地换了写入编号和标题",
    )

    assert unavailable["state"] == "unavailable"
    assert first == replay == buggy_new_operation_replay
    assert first["physical_request_count"] == 0
    assert first["manual_fallback"] is True
    assert len(gateway.calls) == calls_before
    assert len(service.work.query(as_of="2026-08-07")["nodes"]) == 1


def test_legacy_home_intake_http_contract_is_retired(tmp_path: Path) -> None:
    service, _token, gateway = _service(tmp_path)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)

    response = client.post(
        "/api/class-teacher/home/intake/previews",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={
            "text": "明天完成班级材料整理",
            "reference_date": "2026-08-03",
        },
    )

    assert response.status_code == 404
    assert gateway.calls == []


@pytest.mark.skip(reason="B-UI-R7 retired the legacy home-intake HTTP route")
def test_existing_ordinary_store_is_upgraded_before_dispatch_result_is_captured(
    tmp_path: Path,
) -> None:
    service, _token, gateway = _service(tmp_path)
    service.ordinary_database.initialize_schema()
    with closing(service.ordinary_database.connect()) as connection:
        with connection:
            connection.execute("DROP TABLE ordinary_home_intake_draft_versions")
            connection.execute("DROP TABLE ordinary_home_intake_drafts")
            connection.execute(
                "DELETE FROM schema_migrations WHERE migration_name = ?",
                ("005_home_intake_drafts",),
            )

    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    preview_response = client.post(
        "/api/class-teacher/home/intake/previews",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={
            "text": "九月一日开学，提前完成开学准备",
            "reference_date": "2026-08-04",
        },
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()

    response = client.post(
        f"/api/class-teacher/home/intake/previews/{preview['preview_id']}/dispatch",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={
            "fingerprint": preview["fingerprint"],
            "operation_id": "home-existing-ordinary-upgrade-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "succeeded"
    assert body["result_kind"] == "ordinary_plan"
    assert body["draft_id"]
    assert body["result"]["nodes"]
    assert len(gateway.calls) == 1


@pytest.mark.skip(reason="B-UI-R7 retired the legacy home-intake HTTP route")
def test_existing_sensitive_store_is_upgraded_before_dispatch_can_call_model(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={"kind": "affair_recommendation", "summary": "先核对现场事实"},
    )
    with closing(service.database.connect()) as connection:
        with connection:
            connection.execute("DROP TABLE home_intake_draft_versions")
            connection.execute("DROP TABLE home_intake_drafts")
            connection.execute(
                "DELETE FROM schema_migrations WHERE migration_name = ?",
                ("024_home_intake_draft_versions",),
            )

    preview = service.home_intake.prepare(
        token=token,
        text="王小明和张伟发生冲突",
        reference_date="2026-08-04",
    )
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    response = client.post(
        f"/api/class-teacher/home/intake/previews/{preview['preview_id']}/dispatch",
        headers={
            "x-class-teacher-client": "class-teacher-browser-v1",
            "x-class-teacher-session": token,
        },
        json={
            "fingerprint": preview["fingerprint"],
            "operation_id": "home-existing-sensitive-upgrade-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "succeeded"
    assert body["result_kind"] == "affair_recommendation"
    assert body["draft_id"]
    assert len(gateway.calls) == 1


def test_opening_day_plan_recovers_missing_goal_and_singleton_relation_arrays(
    tmp_path: Path,
) -> None:
    service, token, gateway = _service(
        tmp_path,
        result={
            "kind": "plan",
            "assumptions": [],
            "questions": [],
            "nodes": [
                {
                    "id": "1",
                    "kind": "task",
                    "title": "清点开学物资",
                    "details": None,
                    "rationale": "提前发现缺口",
                    "status": ["pending"],
                    "due_date": "2026-08-28",
                },
                {
                    "id": "2",
                    "kind": "task",
                    "title": "确认开学职责",
                    "details": None,
                    "rationale": "保障当天衔接",
                    "status": "pending",
                    "due_date": "2026-08-31",
                },
            ],
            "edges": [
                {"source_id": "2", "target_id": "1", "relation": ["depends_on"]}
            ],
        },
    )
    preview = service.home_intake.prepare(
        token=token,
        text="九月一日正式开学，请帮我安排开学前准备",
        reference_date="2026-08-04",
    )

    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="home-opening-day-normalized-contract-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "ordinary_plan"
    assert result["result"]["nodes"][0]["kind"] == "goal"
    assert result["result"]["nodes"][0]["title"].startswith("九月一日正式开学")
    assert {edge["relation"] for edge in result["result"]["edges"]} == {
        "contains",
        "depends_on",
    }
    assert len(gateway.calls) == 1


@pytest.mark.skip(reason="B-UI-R7 retired the legacy home-intake HTTP route")
def test_dispatch_does_not_call_model_when_draft_store_preflight_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _token, gateway = _service(tmp_path)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    preview_response = client.post(
        "/api/class-teacher/home/intake/previews",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={"text": "九月一日开学", "reference_date": "2026-08-04"},
    )
    preview = preview_response.json()

    def fail_preflight(*, token: str, route: str) -> None:
        _ = token, route
        raise VaultError(
            "class_teacher_work_initialization_failed",
            "普通工作区初始化失败，现有数据没有改变",
            status_code=500,
        )

    monkeypatch.setattr(
        service.home_intake_drafts,
        "prepare_for_dispatch",
        fail_preflight,
    )
    with pytest.raises(ApiError) as caught:
        client.post(
            f"/api/class-teacher/home/intake/previews/{preview['preview_id']}/dispatch",
            headers={"x-class-teacher-client": "class-teacher-browser-v1"},
            json={
                "fingerprint": preview["fingerprint"],
                "operation_id": "home-preflight-failure-001",
            },
        )

    assert caught.value.code == "class_teacher_work_initialization_failed"
    assert gateway.calls == []


@pytest.mark.skip(reason="B-UI-R7 retired the legacy home-intake HTTP route")
def test_sensitive_dispatch_does_not_call_model_when_draft_store_preflight_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token, gateway = _service(tmp_path)
    preview = service.home_intake.prepare(
        token=token,
        text="王小明和张伟发生冲突",
        reference_date="2026-08-04",
    )
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)

    def fail_preflight() -> None:
        raise VaultError(
            "vault_initialization_failed",
            "保险箱初始化失败，没有创建可用数据",
            status_code=500,
        )

    monkeypatch.setattr(service.database, "initialize_schema", fail_preflight)
    with pytest.raises(ApiError) as caught:
        client.post(
            f"/api/class-teacher/home/intake/previews/{preview['preview_id']}/dispatch",
            headers={
                "x-class-teacher-client": "class-teacher-browser-v1",
                "x-class-teacher-session": token,
            },
            json={
                "fingerprint": preview["fingerprint"],
                "operation_id": "home-sensitive-preflight-failure-001",
            },
        )

    assert caught.value.code == "vault_initialization_failed"
    assert gateway.calls == []


@pytest.mark.skip(reason="B-UI-R7 retired the legacy home-intake HTTP route")
def test_parsed_result_remains_visible_when_draft_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _token, gateway = _service(tmp_path)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    preview_response = client.post(
        "/api/class-teacher/home/intake/previews",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={"text": "九月一日开学", "reference_date": "2026-08-04"},
    )
    preview = preview_response.json()

    def fail_capture(*, token: str, operation: dict[str, object]) -> dict[str, object]:
        _ = token, operation
        raise VaultError(
            "home_intake_draft_save_failed",
            "事务草稿保存失败",
            status_code=500,
        )

    monkeypatch.setattr(service.home_intake_drafts, "capture", fail_capture)
    response = client.post(
        f"/api/class-teacher/home/intake/previews/{preview['preview_id']}/dispatch",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={
            "fingerprint": preview["fingerprint"],
            "operation_id": "home-capture-failure-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "succeeded"
    assert body["result_kind"] == "ordinary_plan"
    assert body["result"]["nodes"]
    assert body["draft_id"] is None
    assert body["draft_persistence_error"] == "home_intake_draft_save_failed"
    assert "AI 方案已经返回" in body["draft_persistence_message"]
    assert len(gateway.calls) == 1


@pytest.mark.skip(reason="B-UI-R7 retired the legacy home-intake HTTP route")
def test_home_router_serializes_status_behind_sensitive_dispatch(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class _BlockingGateway(FakeWorkPlanningGateway):
        def invoke(self, **kwargs: object) -> str:
            started.set()
            assert release.wait(timeout=5)
            return super().invoke(**kwargs)

    gateway = _BlockingGateway(
        result={
            "kind": "student_support_recommendation",
            "summary": "先核对已知事实",
        }
    )
    service, token, _gateway = _service(tmp_path, gateway=gateway)
    preview = service.home_intake.prepare(
        token=token,
        text="王小明最近情绪低落",
        reference_date="2026-08-03",
    )
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    headers = {
        "x-class-teacher-client": "class-teacher-browser-v1",
        "x-class-teacher-session": token,
    }

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        dispatched = executor.submit(
            client.post,
            f"/api/class-teacher/home/intake/previews/{preview['preview_id']}/dispatch",
            headers=headers,
            json={
                "fingerprint": preview["fingerprint"],
                "operation_id": "home-concurrent-dispatch-001",
            },
        )
        assert started.wait(timeout=5)
        queried = executor.submit(
            client.get,
            "/api/class-teacher/home/intake/operations/home-concurrent-dispatch-001",
            headers={"x-class-teacher-session": token},
        )
        time.sleep(0.1)
        assert queried.done() is False
        release.set()
        dispatched_response = dispatched.result(timeout=5)
        queried_response = queried.result(timeout=5)

    assert dispatched_response.status_code == 200
    assert queried_response.status_code == 200
    assert dispatched_response.json()["state"] == "succeeded"
    assert queried_response.json()["state"] == "succeeded"
    assert len(gateway.calls) == 1
