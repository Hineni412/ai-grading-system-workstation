from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.routers.jobs import public_job_error
from backend.jobs.store import JobRecord
from backend.llm import LLMGateway
from backend.llm.diagnostics import JsonlDiagnosticJournal
from backend.teaching_prep.api.router import _semester_mapping_job_response
from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.infrastructure.llm import configured as configured_module
from backend.teaching_prep.infrastructure.llm.configured import (
    ActiveProfileSemesterMappingModelAdapter,
)
from backend.teaching_prep.infrastructure.llm.semester_mapping import (
    WorkspaceSemesterMappingModelAdapter,
)
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _ProfileStore:
    def load(self) -> list[dict[str, object]]:
        return [
            {
                "name": "synthetic",
                "config_api_key": "synthetic-key",
                "config_base_url": "https://model.invalid/v1",
                "teaching_prep_model": "synthetic-model",
            }
        ]


class _Completions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {
            "model": "synthetic-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            '{"tree":[],"mappings":[],"uncertainties":[]}'
                        )
                    },
                }
            ],
        }


def _context(tmp_path: Path) -> WorkspaceContext:
    root = tmp_path / "workspaces" / "teaching-prep"
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        migration_project_root=PROJECT_ROOT,
        api_profiles_path=tmp_path / "api_profiles.json",
        legacy_api_profiles_paths=(),
        workspace_dir=lambda module_id, create=False: root,
    )
    return WorkspaceContext(module_id="teaching-prep", root=root, paths=paths)


def test_active_profile_mapping_call_is_visible_in_ai_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "llm_diagnostics.jsonl")
    completions = _Completions()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )

    def gateway_factory(**kwargs: object) -> LLMGateway:
        return LLMGateway(**kwargs, diagnostic_sink=journal)

    monkeypatch.setattr(
        configured_module,
        "LLMGateway",
        gateway_factory,
        raising=False,
    )
    monkeypatch.setattr(
        configured_module,
        "create_openai_client",
        lambda _key, _url: client,
    )
    adapter = ActiveProfileSemesterMappingModelAdapter(
        context=_context(tmp_path),
        profile_store=_ProfileStore(),
    )
    dispatches: list[str] = []

    result = adapter.generate(
        operation_id="semester-mapping-observable-0001",
        semester_snapshot={"synthetic_excerpt": "合成目录片段"},
        dispatch_callback=lambda: dispatches.append("started"),
    )

    assert result == {"tree": [], "mappings": [], "uncertainties": []}
    assert dispatches == ["started"]
    assert len(completions.calls) == 1
    listed = journal.list_calls(limit=10)
    assert listed["returned"] == 1
    call = journal.get_call(str(listed["items"][0]["call_id"]))
    assert call is not None
    assert call["operation_id"] == "semester-mapping-observable-0001"
    assert call["endpoint_host"] == "model.invalid"
    assert call["model"] == "synthetic-model"
    assert call["outcome"] == "success"
    assert call["retry_limit"] == 0
    assert "合成目录片段" in str(call["request"])
    assert call["raw_response"].startswith('{"tree"')


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ({"choices": []}, "semester_mapping_model_response_text_unavailable"),
        (
            {"choices": [{"message": {"content": "not-json"}}]},
            "semester_mapping_model_response_invalid_json",
        ),
        (
            {"choices": [{"message": {"content": "[]"}}]},
            "semester_mapping_model_response_invalid_type",
        ),
    ],
)
def test_mapping_adapter_preserves_safe_response_failure_category(
    response: dict[str, object],
    expected_code: str,
) -> None:
    gateway = SimpleNamespace(
        chat_completions=lambda **_kwargs: response,
    )
    adapter = WorkspaceSemesterMappingModelAdapter(
        gateway=gateway,
        client=object(),
        model="synthetic-model",
    )

    with pytest.raises(TeachingPrepValidationError) as caught:
        adapter.generate(
            operation_id="semester-mapping-invalid-response-0001",
            semester_snapshot={},
        )

    assert getattr(caught.value, "error_code", "") == expected_code


def test_mapping_adapter_does_not_report_dispatch_without_client_transport() -> None:
    gateway = SimpleNamespace(
        chat_completions=lambda **kwargs: kwargs["client"].chat.completions.create()
    )
    adapter = WorkspaceSemesterMappingModelAdapter(
        gateway=gateway,
        client=object(),
        model="synthetic-model",
    )
    dispatches: list[str] = []

    with pytest.raises(TeachingPrepValidationError):
        adapter.generate(
            operation_id="semester-mapping-no-transport-0001",
            semester_snapshot={},
            dispatch_callback=lambda: dispatches.append("started"),
        )

    assert dispatches == []


@pytest.mark.parametrize(
    ("internal_error", "public_error"),
    [
        (
            "semester mapping model response text is unavailable",
            "模型已返回，但没有可读取的正文；可重新检查后手动生成。",
        ),
        (
            "semester mapping model returned invalid JSON",
            "模型已返回，但目录格式不是有效 JSON；可重新检查后手动生成。",
        ),
        (
            "semester mapping model response must be an object",
            "模型已返回，但目录顶层结构不是对象；可重新检查后手动生成。",
        ),
        (
            "semester mapping response failed local validation",
            "模型目录未通过页码和结构校验；可重新检查后手动生成。",
        ),
        (
            "semester mapping model configuration is unavailable",
            "当前备课模型配置不可用，请先检查“大模型 API”设置。",
        ),
    ],
)
def test_mapping_job_exposes_safe_specific_failure_reason(
    internal_error: str,
    public_error: str,
) -> None:
    job = JobRecord(
        id=1,
        job_type="teaching_prep.semester_mapping",
        payload={},
        result={},
        status="failed",
        progress=0.35,
        stage="calling_model",
        detail="模型请求已开始，正在等待返回",
        error=internal_error,
        cancel_requested=False,
        created_at="2026-08-03 00:00:00",
        started_at="2026-08-03 00:00:00",
        updated_at="2026-08-03 00:00:01",
        finished_at="2026-08-03 00:00:01",
    )

    assert _semester_mapping_job_response(job).error == public_error
    assert public_job_error(job) == public_error
