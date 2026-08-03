from __future__ import annotations

from datetime import date
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.ordinary_database import OrdinaryWorkDatabase
from backend.class_teacher.vault_service import VaultService
from backend.class_teacher.work_graph import WorkGraph
from backend.class_teacher.work_planning import FakeWorkPlanningGateway
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _context(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )


def _proposal(
    *,
    due_date: str | None,
    mode: str = "new_work",
) -> dict[str, object]:
    if mode == "progress_update":
        return {
            "kind": "plan",
            "questions": [],
            "assumptions": ["按教师给出的最新情况调整"],
            "nodes": [
                {
                    "id": "next_action",
                    "kind": "communication",
                    "title": "联系场地负责人确认可用时段",
                    "details": "取得明确回复后再继续安排",
                    "status": "pending",
                    "due_date": due_date,
                }
            ],
            "edges": [],
        }
    return {
        "kind": "plan",
        "questions": [],
        "assumptions": ["最终日期由教师确认"],
        "nodes": [
            {
                "id": "goal",
                "kind": "goal",
                "title": "完成本次教师任务",
                "details": "教师最终确认后才正式入图",
                "status": "pending",
                "due_date": due_date,
            },
            {
                "id": "clarify",
                "kind": "decision",
                "title": "核对本次任务的实际约束",
                "details": "仅采用本次输入中的事实",
                "status": "pending",
                "due_date": due_date,
            },
            {
                "id": "deliver",
                "kind": "task",
                "title": "按确认后的要求完成交付",
                "details": None,
                "status": "pending",
                "due_date": due_date,
            },
        ],
        "edges": [
            {"source_id": "goal", "target_id": "clarify", "relation": "contains"},
            {"source_id": "goal", "target_id": "deliver", "relation": "contains"},
            {"source_id": "clarify", "target_id": "deliver", "relation": "next"},
        ],
    }


def _graph(
    tmp_path: Path,
    *,
    result: dict[str, object] | str | None = None,
    available: bool = True,
    unknown: bool = False,
) -> tuple[WorkGraph, FakeWorkPlanningGateway]:
    gateway = FakeWorkPlanningGateway(
        result=result or _proposal(due_date="2026-08-07"),
        available=available,
        unknown=unknown,
    )
    return (
        WorkGraph(OrdinaryWorkDatabase(_context(tmp_path)), model_gateway=gateway),
        gateway,
    )


def _create(
    graph: WorkGraph,
    *,
    text: str = "周五前完成班级资料整理",
    due_date: str = "2026-08-07",
    suffix: str = "001",
) -> dict[str, object]:
    preview = graph.prepare_plan(text=text, due_date=due_date)
    planned = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id=f"work-model-{suffix}",
    )
    return graph.confirm_plan(
        model_operation_id=str(planned["operation_id"]),
        plan_fingerprint=str(planned["plan_fingerprint"]),
        operation_id=f"work-save-{suffix}",
    )


def test_preview_is_exact_network_free_and_has_no_local_fake_steps(tmp_path: Path) -> None:
    graph, gateway = _graph(tmp_path)

    snapshot = graph.query(as_of="2026-08-03")
    preview = graph.prepare_plan(
        text="周五前完成班级资料整理",
        due_date="2026-08-07",
    )

    assert snapshot["nodes"] == []
    assert preview["physical_request_count"] == 0
    assert preview["exact_payload"]["task_text"] == "周五前完成班级资料整理"
    assert preview["exact_payload"]["final_due_date"] == "2026-08-07"
    assert "nodes" not in preview
    assert gateway.calls == []
    assert not graph.database.root.exists()


def test_explicit_natural_date_is_resolved_but_no_step_is_locally_invented(
    tmp_path: Path,
) -> None:
    graph, _gateway = _graph(tmp_path)
    expected = date(date.today().year, 8, 30)
    if expected < date.today():
        expected = date(date.today().year + 1, 8, 30)

    preview = graph.prepare_plan(text="8月30日安排一项班级工作", due_date=None)

    assert preview["final_due_date"] == expected.isoformat()
    assert preview["date_semantics"] == "date-only"
    assert "nodes" not in preview


@pytest.mark.parametrize(
    "source",
    [
        "8.30安排一项班级工作",
        "8/30安排一项班级工作",
        "8-30安排一项班级工作",
        "2026.8.30安排一项班级工作",
        "2026/8/30安排一项班级工作",
        "2026-8-30安排一项班级工作",
    ],
)
def test_common_natural_date_formats_are_supported(
    tmp_path: Path,
    source: str,
) -> None:
    graph, _gateway = _graph(tmp_path)

    preview = graph.prepare_plan(text=source, due_date=None)

    assert str(preview["final_due_date"]).endswith("-08-30")


@pytest.mark.parametrize("source", ["13.30安排工作", "2026/2/30安排工作"])
def test_invalid_common_natural_dates_fail_closed(tmp_path: Path, source: str) -> None:
    graph, _gateway = _graph(tmp_path)

    with pytest.raises(VaultError) as invalid:
        graph.prepare_plan(text=source, due_date=None)

    assert invalid.value.code == "class_teacher_work_date_invalid"


def test_selected_calendar_date_must_match_date_in_task_text(tmp_path: Path) -> None:
    graph, _gateway = _graph(tmp_path)

    with pytest.raises(VaultError) as conflict:
        graph.prepare_plan(
            text="2026年8月30日安排一项班级工作",
            due_date="2026-08-29",
        )

    assert conflict.value.code == "class_teacher_work_date_conflict"
    assert not graph.database.root.exists()


def test_exact_payload_is_called_once_then_teacher_confirms_validated_graph(
    tmp_path: Path,
) -> None:
    graph, gateway = _graph(tmp_path)
    preview = graph.prepare_plan(
        text="周五前完成班级资料整理",
        due_date="2026-08-07",
    )

    first = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-once-001",
    )
    replay = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-once-001",
    )

    assert first == replay
    assert first["state"] == "succeeded"
    assert first["teacher_confirmation_required"] is True
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["payload"] == preview["exact_payload"]
    assert gateway.calls[0]["purpose"] == "ordinary_work_plan"
    assert gateway.calls[0]["data_classification"] == "ordinary"
    assert graph.query(as_of="2026-08-07")["nodes"] == []

    saved = graph.confirm_plan(
        model_operation_id="work-model-once-001",
        plan_fingerprint=str(first["plan_fingerprint"]),
        operation_id="work-save-once-001",
    )
    saved_replay = graph.confirm_plan(
        model_operation_id="work-model-once-001",
        plan_fingerprint=str(first["plan_fingerprint"]),
        operation_id="work-save-once-001",
    )
    snapshot = graph.query(as_of="2026-08-07")

    assert saved_replay == saved
    assert len(snapshot["nodes"]) == 3
    assert len(snapshot["edges"]) == 3
    assert {edge["relation"] for edge in snapshot["edges"]} == {"contains", "next"}


@pytest.mark.parametrize(
    "invalid_result",
    [
        {
            "kind": "plan",
            "questions": [],
            "assumptions": [],
            "nodes": [
                {"id": "goal", "kind": "goal", "title": "目标", "details": None, "status": "pending", "due_date": "2026-08-07"},
                {"id": "step", "kind": "task", "title": "步骤", "details": None, "status": "pending", "due_date": "2026-08-08"},
            ],
            "edges": [{"source_id": "goal", "target_id": "step", "relation": "contains"}],
        },
        {
            "kind": "plan",
            "questions": [],
            "assumptions": [],
            "nodes": [
                {"id": "goal", "kind": "goal", "title": "目标", "details": None, "status": "pending", "due_date": "2026-08-07"},
                {"id": "step", "kind": "task", "title": "步骤", "details": None, "status": "pending", "due_date": "2026-08-07"},
            ],
            "edges": [
                {"source_id": "goal", "target_id": "step", "relation": "contains"},
                {"source_id": "step", "target_id": "goal", "relation": "depends_on"},
            ],
        },
    ],
)
def test_invalid_model_dates_and_cycles_never_reach_work_graph(
    tmp_path: Path,
    invalid_result: dict[str, object],
) -> None:
    graph, gateway = _graph(tmp_path, result=invalid_result)
    preview = graph.prepare_plan(text="周五前完成任务", due_date="2026-08-07")

    result = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-invalid-001",
    )

    assert result["state"] == "invalid_result"
    assert result["physical_request_count"] == 1
    assert len(gateway.calls) == 1
    assert graph.query(as_of="2026-08-07")["nodes"] == []


def test_ordinary_preview_can_be_requested_again_with_a_new_operation(
    tmp_path: Path,
) -> None:
    graph, gateway = _graph(tmp_path, result="not-json")
    preview = graph.prepare_plan(text="周五前完成任务", due_date="2026-08-07")

    first = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-retry-001",
    )
    gateway.result = _proposal(due_date="2026-08-07")
    second = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-retry-002",
    )

    assert first["state"] == "invalid_result"
    assert second["state"] == "succeeded"
    assert len(gateway.calls) == 2


def test_work_plan_receipt_reports_all_gateway_retry_attempts(tmp_path: Path) -> None:
    graph, gateway = _graph(tmp_path, result=_proposal(due_date="2026-08-07"))
    gateway.request_count = 3
    preview = graph.prepare_plan(text="周五前完成任务", due_date="2026-08-07")

    result = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-three-attempts-001",
    )

    assert result["state"] == "succeeded"
    assert result["physical_request_count"] == 3


def test_first_follow_up_becomes_broad_draft_and_unknown_result_does_not_retry(tmp_path: Path) -> None:
    follow_up = {
        "kind": "follow_up",
        "questions": ["最终需要在哪一天完成？"],
        "assumptions": [],
        "nodes": [],
        "edges": [],
    }
    graph, gateway = _graph(tmp_path, result=follow_up)
    preview = graph.prepare_plan(text="安排本次工作", due_date=None)
    result = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-question-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "ordinary_plan"
    assert result["questions"] == ["最终需要在哪一天完成？"]
    assert len(result["plan"]["nodes"]) == 3
    assert result["plan_fingerprint"]
    assert len(gateway.calls) == 1

    unknown_graph, unknown_gateway = _graph(tmp_path / "unknown", unknown=True)
    unknown_preview = unknown_graph.prepare_plan(text="安排另一项工作", due_date=None)
    unknown = unknown_graph.invoke_plan(
        preview_id=str(unknown_preview["preview_id"]),
        fingerprint=str(unknown_preview["fingerprint"]),
        operation_id="work-model-unknown-001",
    )
    unknown_replay = unknown_graph.invoke_plan(
        preview_id=str(unknown_preview["preview_id"]),
        fingerprint=str(unknown_preview["fingerprint"]),
        operation_id="work-model-unknown-001",
    )
    assert unknown["state"] == "result_unknown"
    assert unknown_replay == unknown
    assert len(unknown_gateway.calls) == 1


@pytest.mark.parametrize(
    "result",
    [
        {
            "kind": "follow_up",
            "questions": ["王小明最近是否已经确诊？"],
            "assumptions": [],
            "nodes": [],
            "edges": [],
        },
        {
            "kind": "plan",
            "questions": [],
            "assumptions": ["系统自动发送通知并自动结案"],
            "nodes": [
                {"id": "goal", "kind": "goal", "title": "完成任务", "details": None, "status": "pending", "due_date": "2026-08-07"},
            ],
            "edges": [],
        },
        {
            "kind": "plan",
            "questions": [],
            "assumptions": [],
            "nodes": [
                {"id": "goal", "kind": "goal", "title": "认定该事件属于欺凌并给予处分", "details": None, "status": "pending", "due_date": "2026-08-07"},
            ],
            "edges": [],
        },
    ],
)
def test_sensitive_or_decision_making_model_output_is_rejected(
    tmp_path: Path,
    result: dict[str, object],
) -> None:
    graph, _gateway = _graph(tmp_path, result=result)
    preview = graph.prepare_plan(text="周五前完成普通工作", due_date="2026-08-07")

    planned = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-policy-001",
    )

    assert planned["state"] == "invalid_result"
    assert planned["plan"] is None


def test_arbitrary_placeholder_year_is_not_repaired(tmp_path: Path) -> None:
    graph, gateway = _graph(
        tmp_path,
        result={
            "kind": "plan",
            "questions": [],
            "assumptions": [],
            "nodes": [
                {
                    "id": "goal",
                    "kind": "goal",
                    "title": "完成开学准备",
                    "details": None,
                    "status": "pending",
                    "due_date": "999X-08-25",
                }
            ],
            "edges": [],
        },
    )
    preview = graph.prepare_plan(text="九月一日开学", due_date="2026-09-01")

    planned = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-invalid-placeholder-year-001",
    )

    assert planned["state"] == "invalid_result"
    assert planned["plan"] is None
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    "text",
    (
        "提醒王小明交材料",
        "王小明8月30日交材料",
        "通知王小明8月30日交材料",
        "请王小明8.30交材料",
        "让王小明明天交材料",
        "给王小明安排任务",
        "通知班级里的王小明8月30日交材料",
        "提醒全班的王小明明天交材料",
        "通知班级中的王小明8月30日交材料",
        "提醒全班同学中的王小明明天交材料",
        "提醒全班内王小明明天交材料",
    ),
)
def test_common_name_phrasings_are_routed_out_of_ordinary_work(
    tmp_path: Path,
    text: str,
) -> None:
    graph, _gateway = _graph(tmp_path)

    with pytest.raises(VaultError) as sensitive:
        graph.prepare_plan(text=text, due_date=None)
    assert sensitive.value.code == "class_teacher_work_sensitive_content"


@pytest.mark.parametrize(
    "text",
    (
        "通知学校8月30日交材料",
        "通知全校8月30日交材料",
        "给全校安排任务",
        "通知班级8月30日交材料",
        "通知班会明天召开",
        "班会明天召开",
        "周会明天召开",
        "通知全班同学8月30日交材料",
        "通知各班同学8月30日交材料",
        "提醒全班同学明天交材料",
    ),
)
def test_organization_phrasings_remain_in_ordinary_work(
    tmp_path: Path,
    text: str,
) -> None:
    graph, _gateway = _graph(tmp_path)

    ordinary = graph.prepare_plan(text=text, due_date=None)
    assert ordinary["exact_payload"]["task_text"] == text


def test_destination_change_and_late_configuration_make_zero_requests(
    tmp_path: Path,
) -> None:
    graph, gateway = _graph(tmp_path)
    preview = graph.prepare_plan(text="安排普通工作", due_date="2026-08-07")
    gateway.model_endpoint = "https://changed.invalid/v1"

    changed = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-destination-change-001",
    )

    assert changed["state"] == "destination_changed"
    assert changed["physical_request_count"] == 0
    assert gateway.calls == []

    disabled_graph, disabled_gateway = _graph(tmp_path / "disabled", available=False)
    disabled_preview = disabled_graph.prepare_plan(
        text="安排另一项普通工作",
        due_date="2026-08-07",
    )
    disabled_gateway.available = True
    late = disabled_graph.invoke_plan(
        preview_id=str(disabled_preview["preview_id"]),
        fingerprint=str(disabled_preview["fingerprint"]),
        operation_id="work-late-config-001",
    )
    assert late["state"] == "destination_changed"
    assert late["physical_request_count"] == 0
    assert disabled_gateway.calls == []


def test_status_reports_in_progress_without_marking_live_call_unknown(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class _BlockingGateway(FakeWorkPlanningGateway):
        def invoke(self, **kwargs: object) -> str:
            started.set()
            assert release.wait(timeout=5)
            return super().invoke(**kwargs)

    gateway = _BlockingGateway(result=_proposal(due_date="2026-08-07"))
    graph = WorkGraph(
        OrdinaryWorkDatabase(_context(tmp_path)),
        model_gateway=gateway,
    )
    preview = graph.prepare_plan(text="安排普通工作", due_date="2026-08-07")
    returned: list[dict[str, object]] = []
    worker = threading.Thread(
        target=lambda: returned.append(
            graph.invoke_plan(
                preview_id=str(preview["preview_id"]),
                fingerprint=str(preview["fingerprint"]),
                operation_id="work-inflight-001",
            )
        )
    )
    worker.start()
    assert started.wait(timeout=5)

    status = graph.plan_status(operation_id="work-inflight-001")
    assert status["state"] == "in_progress"
    assert status["physical_request_count"] == 0

    conflicting_preview = graph.prepare_plan(
        text="另一项普通工作",
        due_date="2026-08-07",
    )
    with pytest.raises(VaultError) as conflict:
        graph.invoke_plan(
            preview_id=str(conflicting_preview["preview_id"]),
            fingerprint=str(conflicting_preview["fingerprint"]),
            operation_id="work-inflight-001",
        )
    assert conflict.value.code == "class_teacher_operation_conflict"

    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert returned[0]["state"] == "succeeded"
    assert len(gateway.calls) == 1


def test_latest_situation_uses_same_ai_double_confirmation_flow(tmp_path: Path) -> None:
    graph, gateway = _graph(tmp_path)
    created = _create(graph, suffix="progress-parent")
    parent = created["nodes"][1]
    gateway.result = _proposal(due_date="2026-08-07", mode="progress_update")

    preview = graph.prepare_plan(
        parent_node_id=str(parent["node_id"]),
        parent_revision=int(parent["revision"]),
        text="场地尚未确认，需要调整下一步",
        due_date="2026-08-07",
    )
    planned = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="work-model-progress-001",
    )
    before = graph.query(as_of="2026-08-07")
    confirmed = graph.confirm_plan(
        model_operation_id="work-model-progress-001",
        plan_fingerprint=str(planned["plan_fingerprint"]),
        operation_id="work-save-progress-001",
    )

    assert not any(edge["relation"] == "review_of" for edge in before["edges"])
    assert confirmed["parent_node_id"] == parent["node_id"]
    assert confirmed["edges"][-1]["relation"] == "review_of"


def test_revision_sensitive_text_and_projection_safety_are_preserved(tmp_path: Path) -> None:
    graph, _gateway = _graph(tmp_path)
    created = _create(graph, suffix="security")
    node = created["nodes"][1]
    graph.update_node(
        node_id=str(node["node_id"]),
        revision=int(node["revision"]),
        status="completed",
        due_date="2026-08-07",
        operation_id="work-update-first",
    )

    with pytest.raises(VaultError) as conflict:
        graph.update_node(
            node_id=str(node["node_id"]),
            revision=int(node["revision"]),
            status="waiting",
            due_date="2026-08-07",
            operation_id="work-update-stale",
        )
    with pytest.raises(VaultError) as sensitive:
        graph.prepare_plan(text="联系张三同学家长", due_date=None)

    assert conflict.value.code == "class_teacher_work_revision_conflict"
    assert sensitive.value.code == "class_teacher_work_sensitive_content"

    payload = graph.enqueue_sensitive_projection(
        projection_id="projection-001",
        due_date="2026-08-09",
        status="pending",
        source_revision=2,
        operation_id="projection-op-001",
    )
    assert payload["title"] == "学生事项待跟进"


def test_work_preview_api_is_available_while_sensitive_vault_is_locked(
    tmp_path: Path,
) -> None:
    service = VaultService(_context(tmp_path))
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)

    empty = client.get("/api/class-teacher/work?as_of=2026-08-03")
    previewed = client.post(
        "/api/class-teacher/work/plans/previews",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={"text": "准备开学工作", "due_date": "2026-08-05"},
    )

    assert empty.status_code == 200
    assert empty.json()["nodes"] == []
    assert previewed.status_code == 200
    assert previewed.json()["model_enabled"] is False
    assert previewed.json()["physical_request_count"] == 0
    assert service.status()["initialized"] is False
    assert not service.ordinary_database.exists
