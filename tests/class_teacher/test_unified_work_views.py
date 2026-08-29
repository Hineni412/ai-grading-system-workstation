from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.ordinary_database import OrdinaryWorkDatabase
from backend.class_teacher.work_graph import WorkGraph
from backend.class_teacher.work_planning import FakeWorkPlanningGateway
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _graph(tmp_path: Path) -> WorkGraph:
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )
    return WorkGraph(
        OrdinaryWorkDatabase(context),
        model_gateway=FakeWorkPlanningGateway(result={
            "kind": "plan",
            "questions": [],
            "assumptions": [],
            "nodes": [{
                "id": "goal",
                "kind": "goal",
                "title": "完成合成班务",
                "details": "只包含普通信息",
                "status": "pending",
                "due_date": "2026-08-06",
            }],
            "edges": [],
        }),
    )


def _create(graph: WorkGraph) -> dict[str, object]:
    preview = graph.prepare_plan(text="8月6日前完成合成班务", due_date="2026-08-06")
    invoked = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="unified-work-model-001",
    )
    return graph.confirm_plan(
        model_operation_id=str(invoked["operation_id"]),
        plan_fingerprint=str(invoked["plan_fingerprint"]),
        operation_id="unified-work-save-001",
    )


def test_today_and_calendar_read_the_same_work_graph(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    created = _create(graph)
    node_id = str(created["nodes"][0]["node_id"])

    today = graph.read(view="today", anchor="2026-08-06")
    week = graph.read(view="week", anchor="2026-08-06")

    assert [item["node_id"] for item in today["nodes"]] == [node_id]
    assert [item["node_id"] for item in week["nodes"]] == [node_id]
    assert today["source_version"] == week["source_version"]


def test_progress_and_collection_history_are_read_through_node_detail(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    created = _create(graph)
    node = created["nodes"][0]

    detail = graph.command(
        node_id=str(node["node_id"]),
        command="record_progress",
        expected_revision=int(node["revision"]),
        operation_id="unified-work-progress-001",
        progress="已完成材料初步核对。",
    )

    assert detail["progress_events"][0]["summary"] == "已完成材料初步核对。"
    assert detail["allowed_commands"] == ["update_status", "reschedule", "record_progress", "delete"]


def test_progress_response_loss_replays_once_and_payload_reuse_conflicts(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    node = _create(graph)["nodes"][0]
    command = {
        "node_id": str(node["node_id"]),
        "command": "record_progress",
        "expected_revision": int(node["revision"]),
        "operation_id": "unified-work-progress-replay",
        "progress": "只写入一次的合成进展。",
    }

    first = graph.command(**command)
    replay = graph.command(**command)

    assert replay == first
    assert len(replay["progress_events"]) == 1
    with pytest.raises(VaultError) as conflict:
        graph.command(**{**command, "progress": "同一操作编号的不同内容。"})
    assert conflict.value.code == "class_teacher_operation_conflict"


def test_status_response_loss_replays_original_result_after_later_reschedule(
    tmp_path: Path,
) -> None:
    graph = _graph(tmp_path)
    node = _create(graph)["nodes"][0]
    status_command = {
        "node_id": str(node["node_id"]),
        "command": "update_status",
        "expected_revision": int(node["revision"]),
        "operation_id": "unified-work-status-replay",
        "status": "in_progress",
    }

    first = graph.command(**status_command)
    graph.command(
        node_id=str(node["node_id"]),
        command="reschedule",
        expected_revision=int(first["revision"]),
        operation_id="unified-work-reschedule-later",
        due_date="2026-08-09",
    )

    assert graph.command(**status_command) == first


def test_progress_write_rolls_back_when_receipt_cannot_be_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _graph(tmp_path)
    node = _create(graph)["nodes"][0]

    def fail_receipt(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic receipt failure")

    monkeypatch.setattr(graph, "_remember", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt failure"):
        graph.command(
            node_id=str(node["node_id"]),
            command="record_progress",
            expected_revision=int(node["revision"]),
            operation_id="unified-work-progress-rollback",
            progress="这条进展必须随回执一起回滚。",
        )

    detail = graph.detail(node_id=str(node["node_id"]))
    assert detail["node"]["revision"] == node["revision"]
    assert detail["progress_events"] == []
