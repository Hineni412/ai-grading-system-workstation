from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.routers.jobs import public_job_error
from backend.jobs.store import JobRecord
from backend.llm import LLMGateway
from backend.llm.diagnostics import JsonlDiagnosticJournal
from backend.teaching_prep.api.router import _semester_mapping_job_response
from backend.teaching_prep.application.semester_mapping import (
    validate_semester_mapping_payload,
)
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
                            '{"annotations":[],"matches":[],"uncertainties":[]}'
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
        semester_snapshot={
            "semester": {
                "semester_id": "s" * 32,
                "school_year": "2026",
                "term": "第一学期",
                "planned_new_lesson_count": 1,
                "revision": 3,
                "curriculum_id": "c" * 32,
                "curriculum_title": "合成课程",
            },
            "lessons": [],
            "materials": [
                {
                    "record_id": "m" * 32,
                    "display_name": "合成资料",
                    "material_role": "exercise_workbook",
                    "current_version_id": "v" * 32,
                    "record_revision": 7,
                    "unit_count": 1,
                    "units": [
                        {
                            "unit_index": 1,
                            "title": "第1页",
                            "text_excerpt": "合成目录片段",
                            "text_status": "ready",
                            "object_summary": {"preview_kind": "pdf_page"},
                            "preview_sha256": "a" * 64,
                        }
                    ],
                }
            ],
        },
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
    assert call["raw_response"].startswith('{"annotations"')
    sent = completions.calls[0]
    assert sent["max_tokens"] == 20_000
    messages = sent["messages"]
    assert isinstance(messages, list)
    model_snapshot = json.loads(str(messages[1]["content"]))
    assert "planned_new_lesson_count" not in model_snapshot["semester"]
    material = model_snapshot["materials"][0]
    assert set(material) == {
        "display_name",
        "material_role",
        "record_id",
        "unit_count",
    }
    assert model_snapshot["directory_evidence"]["full_page_text_sent"] is False
    assert model_snapshot["directory_evidence"]["anchors"][0]["text_excerpt"] == (
        "合成目录片段"
    )
    assert "课时分钟数" in str(messages[0]["content"])
    assert "不能返回任何页码" in str(messages[0]["content"])


def test_existing_tree_mapping_call_only_offers_formal_lesson_ids() -> None:
    captured: dict[str, object] = {}

    def chat_completions(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"annotations":[],"matches":[{"lesson_ref":"'
                            + "l" * 32
                            + '","evidence_ids":[],"basis":"资料内容无法对应"}],'
                            '"uncertainties":[]}'
                        )
                    }
                }
            ]
        }

    adapter = WorkspaceSemesterMappingModelAdapter(
        gateway=SimpleNamespace(chat_completions=chat_completions),
        client=object(),
        model="synthetic-model",
    )
    lesson_id = "l" * 32

    result = adapter.generate(
        operation_id="semester-mapping-existing-tree-0001",
        semester_snapshot={
            "semester": {
                "school_year": "2026-2027",
                "term": "first",
                "planned_new_lesson_count": 48,
                "curriculum_title": "北师大",
            },
            "lessons": [
                {
                    "id": "c" * 32,
                    "parent_id": None,
                    "node_type": "chapter",
                    "title": "第一章",
                    "sort_order": 1,
                    "duration_minutes": None,
                },
                {
                    "id": lesson_id,
                    "parent_id": "c" * 32,
                    "node_type": "lesson",
                    "title": "第1课时 算术平方根",
                    "sort_order": 1,
                    "duration_minutes": 45,
                },
            ],
            "materials": [
                {
                    "record_id": "m" * 32,
                    "display_name": "合成教辅",
                    "material_role": "exercise_workbook",
                    "unit_count": 67,
                }
            ],
            "directory_evidence": {
                "strategy": "sparse_outline",
                "anchors": [],
                "confidence": "low",
            },
        },
    )

    assert result == {
        "tree": [],
        "mappings": [],
        "uncertainties": [
            "第1课时 算术平方根暂未对应：资料内容无法对应"
        ],
    }
    request_kwargs = captured["kwargs"]
    assert isinstance(request_kwargs, dict)
    messages = request_kwargs["messages"]
    assert isinstance(messages, list)
    system_instruction = str(messages[0]["content"])
    model_snapshot = json.loads(str(messages[1]["content"]))
    assert "proposal:" not in system_instruction
    assert '"matches"' in system_instruction
    assert "不能返回任何页码" in system_instruction
    assert model_snapshot["mapping_mode"] == "map_existing_lessons"
    assert model_snapshot["available_lessons"] == [
        {
            "chapter_title": "第一章",
            "duration_minutes": 45,
            "id": lesson_id,
            "title": "第1课时 算术平方根",
        }
    ]
    assert "lessons" not in model_snapshot
    assert "planned_new_lesson_count" not in model_snapshot["semester"]


def test_mapping_adapter_sends_located_page_image_but_materializes_local_range() -> None:
    captured: dict[str, object] = {}

    def chat_completions(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "choices": [{
                "message": {
                    "content": (
                        '{"annotations":[{"evidence_id":"toc-001",'
                        '"title":"第1课时 探索勾股定理","chapter_title":"第一章",'
                        '"section_title":"第一节","kind":"lesson"}],'
                        '"matches":[],"uncertainties":[]}'
                    )
                }
            }]
        }

    adapter = WorkspaceSemesterMappingModelAdapter(
        gateway=SimpleNamespace(chat_completions=chat_completions),
        client=object(),
        model="synthetic-model",
    )
    result = adapter.generate(
        operation_id="semester-mapping-image-contract-0001",
        semester_snapshot={
            "semester": {"school_year": "2026", "term": "first"},
            "lessons": [],
            "materials": [{
                "record_id": "m" * 32,
                "display_name": "合成教辅",
                "material_role": "exercise_workbook",
                "unit_count": 10,
            }],
            "directory_evidence": {
                "strategy": "toc_calibrated",
                "toc_entries": [{
                    "evidence_id": "toc-001",
                    "title": "探索勾股定理",
                    "printed_page": 1,
                    "source_unit": 2,
                }],
                "resolved_ranges": [{
                    "evidence_id": "range-001",
                    "toc_evidence_id": "toc-001",
                    "start_unit": 3,
                    "end_unit": 5,
                }],
                "anchors": [],
            },
            "directory_page_images": [{
                "unit_index": 2,
                "mime_type": "image/png",
                "content": b"synthetic-image",
            }],
        },
    )

    assert result["mappings"][0]["start_unit"] == 3
    assert result["mappings"][0]["end_unit"] == 5
    messages = captured["kwargs"]["messages"]
    content = messages[1]["content"]
    assert isinstance(content, list)
    assert content[-1]["type"] == "image_url"
    assert content[-1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert "start_unit" not in str(messages[0]["content"])


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ({"choices": []}, "semester_mapping_model_response_text_unavailable"),
        (
            {"choices": [{"message": {"content": "not-json"}}]},
            "semester_mapping_model_response_invalid_json",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"tree":['},
                    }
                ]
            },
            "semester_mapping_model_response_truncated",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"tree":['},
                    }
                ]
            },
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


def test_diagnostic_journal_distinguishes_truncated_json(tmp_path: Path) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "llm_diagnostics.jsonl")
    journal.record_response(
        operation_id="semester-mapping-truncated-diagnostic-0001",
        request_id="semester-mapping-truncated-diagnostic-0001",
        attempt=1,
        request_kind="workspace",
        protocol="chat_completions",
        model="synthetic-model",
        endpoint_host="model.invalid",
        response={
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"tree":['},
                }
            ]
        },
        elapsed_ms=123,
    )

    listed = journal.list_calls(limit=10)
    call = journal.get_call(str(listed["items"][0]["call_id"]))
    assert call is not None
    assert call["parse_status"] == "truncated_json"
    assert "长度上限" in str(call["parse_error"])


def test_mapping_validation_assigns_global_machine_keys_locally() -> None:
    record_id = "m" * 32
    raw = {
        "tree": [
            {
                "key": "chapter_1",
                "title": "第一章",
                "sections": [
                    {
                        "key": "section_1",
                        "title": "第一节",
                        "lessons": [
                            {
                                "key": "lesson_a",
                                "title": "课时一",
                                "duration_minutes": 45,
                            }
                        ],
                    }
                ],
            },
            {
                "key": "chapter_2",
                "title": "第二章",
                "sections": [
                    {
                        "key": "section_1",
                        "title": "第一节",
                        "lessons": [
                            {
                                "key": "lesson_b",
                                "title": "课时二",
                                "duration_minutes": 45,
                            }
                        ],
                    }
                ],
            },
        ],
        "mappings": [
            {
                "material_record_id": record_id,
                "lesson_ref": "proposal:lesson_a",
                "start_unit": 1,
                "end_unit": 1,
            },
            {
                "material_record_id": record_id,
                "lesson_ref": "proposal:lesson_b",
                "start_unit": 2,
                "end_unit": 2,
            },
        ],
        "uncertainties": [],
    }
    snapshot = {
        "lessons": [],
        "materials": [
            {
                "record_id": record_id,
                "material_role": "exercise_workbook",
                "unit_count": 2,
            }
        ],
    }

    normalized = validate_semester_mapping_payload(raw, snapshot=snapshot)

    first_section = normalized["tree"][0]["sections"][0]
    second_section = normalized["tree"][1]["sections"][0]
    assert first_section["key"] == "section_001_001"
    assert second_section["key"] == "section_002_001"
    assert [item["lesson_ref"] for item in normalized["mappings"]] == [
        "proposal:lesson_001_001_001",
        "proposal:lesson_002_001_001",
    ]


def test_mapping_validation_rejects_empty_lesson_sections() -> None:
    raw = {
        "tree": [
            {
                "key": "chapter_1",
                "title": "第一章",
                "sections": [
                    {
                        "key": "section_1",
                        "title": "第一节",
                        "lessons": [],
                    }
                ],
            }
        ],
        "mappings": [],
        "uncertainties": ["资料不足"],
    }

    with pytest.raises(
        TeachingPrepValidationError,
        match="at least one lesson",
    ):
        validate_semester_mapping_payload(
            raw,
            snapshot={"lessons": [], "materials": []},
        )


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
            "semester mapping model output was truncated",
            "模型输出达到长度上限，目录没有完整返回；系统未自动重试。",
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
            "semester mapping model attempted to replace the existing "
            "lesson tree",
            "模型尝试重建已有课时目录，本次建议已拦截；请重新生成映射。",
        ),
        (
            "semester mapping model referred to a lesson outside the "
            "existing tree",
            "模型引用了当前目录中不存在的课时，本次建议已拦截；请重新生成映射。",
        ),
        (
            "semester mapping model omitted uncertainty for unmapped pages",
            "模型没有说明未映射的资料页，本次建议已拦截；请重新生成映射。",
        ),
        (
            "semester mapping model configuration is unavailable",
            "当前备课模型配置不可用，请先检查“大模型 API”设置。",
        ),
        (
            "semester mapping model request parameter is incompatible",
            "当前模型不接受目录请求参数，请检查模型配置后手动生成。",
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
