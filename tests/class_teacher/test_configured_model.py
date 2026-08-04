from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.configured_model import (
    ActiveProfileApprovedModelGateway,
)
from backend.class_teacher.feature import _create_service
from backend.class_teacher.model_approval import (
    ModelDestinationChanged,
    ModelDispatchDisabled,
)
from backend.class_teacher.ordinary_database import OrdinaryWorkDatabase
from backend.class_teacher.work_graph import WorkGraph
from backend.workspaces.contracts import WorkspaceContext
from backend.workspaces.model_policy import WorkspaceModelRequest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _ProfileStore:
    def __init__(self, profiles: list[dict[str, object]]) -> None:
        self.profiles = profiles
        self.loads = 0

    def load(self) -> list[dict[str, object]]:
        self.loads += 1
        return [dict(item) for item in self.profiles]


class _RecordingWorkspaceGateway:
    def __init__(self, **construction: object) -> None:
        self.construction = construction
        self.calls: list[dict[str, object]] = []
        self.physical_request_count = 3

    def chat_completions(self, **call: object) -> dict[str, object]:
        self.calls.append(call)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"kind":"follow_up","questions":["哪一天？"]}'
                    }
                }
            ]
        }


class _JsonKeywordEnforcingWorkspaceGateway(_RecordingWorkspaceGateway):
    """Model the provider rule shown by the integration-preview failure."""

    def chat_completions(self, **call: object) -> dict[str, object]:
        kwargs = call["kwargs"]
        assert isinstance(kwargs, dict)
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        combined = " ".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict)
        )
        if (
            kwargs.get("response_format") == {"type": "json_object"}
            and "json" not in combined
        ):
            raise ValueError(
                "messages must contain the word 'json' to use json_object"
            )
        return super().chat_completions(**call)


def _context(tmp_path: Path) -> WorkspaceContext:
    root = tmp_path / "workspaces" / "class-teacher"
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        migration_project_root=PROJECT_ROOT,
        api_profiles_path=tmp_path / "api_profiles.json",
        legacy_api_profiles_paths=(),
        workspace_dir=lambda module_id, create=False: root,
    )
    return WorkspaceContext(module_id="class-teacher", root=root, paths=paths)


def _clear_model_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LLM_CONFIG_API_KEY",
        "LLM_API_KEY",
        "LLM_CONFIG_BASE_URL",
        "LLM_BASE_URL",
        "LLM_CONFIG_MODEL",
        "LLM_GRADING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_production_feature_uses_dynamic_profile_gateway_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_model_environment(monkeypatch)
    context = _context(tmp_path)

    service = _create_service(context)

    assert isinstance(
        service.model_approval.gateway,
        ActiveProfileApprovedModelGateway,
    )
    assert service.model_approval.gateway.is_available() is False
    assert not context.root.exists()


def test_active_profile_gateway_is_default_off_until_configuration_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_model_environment(monkeypatch)
    constructed: list[_RecordingWorkspaceGateway] = []

    def gateway_factory(**kwargs: object) -> _RecordingWorkspaceGateway:
        gateway = _RecordingWorkspaceGateway(**kwargs)
        constructed.append(gateway)
        return gateway

    gateway = ActiveProfileApprovedModelGateway(
        context=_context(tmp_path),
        profile_store=_ProfileStore([]),
        gateway_factory=gateway_factory,
        client_factory=lambda _key, _url: object(),
    )

    assert gateway.is_available() is False
    assert gateway.model_name == "未启用真实模型"
    with pytest.raises(ModelDispatchDisabled):
        gateway.invoke(payload={"task_text": "合成内容"}, operation_id="model-op-001")
    assert constructed == []


def test_active_profile_gateway_sends_only_confirmed_canonical_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_model_environment(monkeypatch)
    constructed: list[_RecordingWorkspaceGateway] = []
    clients: list[tuple[str, str]] = []

    def gateway_factory(**kwargs: object) -> _RecordingWorkspaceGateway:
        gateway = _RecordingWorkspaceGateway(**kwargs)
        constructed.append(gateway)
        return gateway

    def client_factory(key: str, url: str) -> object:
        clients.append((key, url))
        return object()

    gateway = ActiveProfileApprovedModelGateway(
        context=_context(tmp_path),
        profile_store=_ProfileStore(
            [
                {
                    "name": "synthetic",
                    "config_api_key": "synthetic-key",
                    "config_base_url": "https://model.invalid/v1",
                    "config_model": "synthetic-model",
                }
            ]
        ),
        gateway_factory=gateway_factory,
        client_factory=client_factory,
    )
    payload = {
        "purpose": "student_support_note",
        "student_alias": "学生A",
        "task_text": "学生A希望调整作业节奏",
        "instructions": "只生成待教师复核的中性草稿。",
    }

    assert gateway.is_available() is True
    assert gateway.model_name == "synthetic-model"
    assert constructed == []
    result = gateway.invoke(payload=payload, operation_id="model-op-001")

    assert json.loads(result) == {"kind": "follow_up", "questions": ["哪一天？"]}
    assert gateway.physical_request_count("model-op-001") == 3
    assert clients == [("synthetic-key", "https://model.invalid/v1")]
    assert len(constructed) == 1
    assert constructed[0].construction["metadata_only"] is False
    assert constructed[0].construction["claim_operations"] is False
    assert constructed[0].construction["allow_retry"] is True
    call = constructed[0].calls[0]
    request = call["request"]
    assert isinstance(request, WorkspaceModelRequest)
    assert request.operation_id == "model-op-001"
    assert request.data_classification == "restricted_anonymized"
    assert call["kwargs"] == {
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ],
        "response_format": {"type": "json_object"},
    }


def test_json_object_request_explicitly_instructs_the_model_to_return_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_model_environment(monkeypatch)
    constructed: list[_JsonKeywordEnforcingWorkspaceGateway] = []

    def gateway_factory(**kwargs: object) -> _JsonKeywordEnforcingWorkspaceGateway:
        gateway = _JsonKeywordEnforcingWorkspaceGateway(**kwargs)
        constructed.append(gateway)
        return gateway

    gateway = ActiveProfileApprovedModelGateway(
        context=_context(tmp_path),
        profile_store=_ProfileStore(
            [
                {
                    "name": "synthetic",
                    "config_api_key": "synthetic-key",
                    "config_base_url": "https://model.invalid/v1",
                    "config_model": "synthetic-model",
                }
            ]
        ),
        gateway_factory=gateway_factory,
        client_factory=lambda _key, _url: object(),
    )

    graph = WorkGraph(
        OrdinaryWorkDatabase(_context(tmp_path)),
        model_gateway=gateway,
    )
    preview = graph.prepare_plan(
        text="整理家长会准备事项",
        due_date="2026-08-07",
    )

    result = graph.invoke_plan(
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="model-json-contract-001",
    )

    assert result["state"] == "succeeded"
    assert result["result_kind"] == "ordinary_plan"
    assert len(constructed[0].calls) == 1


def test_destination_snapshot_is_safe_and_invoke_reuses_one_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_model_environment(monkeypatch)
    store = _ProfileStore(
        [
            {
                "name": "synthetic",
                "config_api_key": "credential-must-never-appear",
                "config_base_url": "https://model.invalid/v1?token=hidden",
                "config_model": "synthetic-model",
            }
        ]
    )
    constructed: list[_RecordingWorkspaceGateway] = []

    def gateway_factory(**kwargs: object) -> _RecordingWorkspaceGateway:
        gateway = _RecordingWorkspaceGateway(**kwargs)
        constructed.append(gateway)
        return gateway

    gateway = ActiveProfileApprovedModelGateway(
        context=_context(tmp_path),
        profile_store=store,
        gateway_factory=gateway_factory,
        client_factory=lambda _key, _url: object(),
    )

    destination = gateway.destination_snapshot()

    assert destination["available"] is True
    assert destination["model_provider"] == "model.invalid"
    assert destination["model_endpoint"] == "https://model.invalid/v1"
    assert destination["model"] == "synthetic-model"
    assert "credential" not in json.dumps(destination)
    assert "hidden" not in json.dumps(destination)
    loads_before_invoke = store.loads

    gateway.invoke(
        payload={"task_text": "合成内容"},
        operation_id="model-destination-stable-001",
        expected_destination_fingerprint=str(destination["destination_fingerprint"]),
    )

    assert store.loads == loads_before_invoke + 1
    assert len(constructed) == 1


def test_destination_change_is_rejected_before_gateway_or_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_model_environment(monkeypatch)
    store = _ProfileStore(
        [
            {
                "name": "synthetic",
                "config_api_key": "synthetic-key",
                "config_base_url": "https://first.invalid/v1",
                "config_model": "first-model",
            }
        ]
    )
    constructed: list[_RecordingWorkspaceGateway] = []
    clients: list[tuple[str, str]] = []
    gateway = ActiveProfileApprovedModelGateway(
        context=_context(tmp_path),
        profile_store=store,
        gateway_factory=lambda **kwargs: (
            constructed.append(_RecordingWorkspaceGateway(**kwargs))
            or constructed[-1]
        ),
        client_factory=lambda key, url: clients.append((key, url)) or object(),
    )
    preview_destination = gateway.destination_snapshot()
    store.profiles[0]["config_base_url"] = "https://second.invalid/v1"

    with pytest.raises(ModelDestinationChanged):
        gateway.invoke(
            payload={"task_text": "合成内容"},
            operation_id="model-destination-change-001",
            expected_destination_fingerprint=str(
                preview_destination["destination_fingerprint"]
            ),
        )

    assert constructed == []
    assert clients == []
