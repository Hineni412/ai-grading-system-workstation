from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import os
import zipfile

import pytest

from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
)
from backend.teaching_prep.application.workbench_iteration import (
    normalize_exercise_suggestion_payload,
)
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.infrastructure.fakes import (
    FakeExerciseSuggestionModelAdapter,
    FakeWpsAdapter,
)
from backend.teaching_prep.infrastructure.llm.exercise_suggestions import (
    WorkspaceExerciseSuggestionModelAdapter,
)
from backend.teaching_prep.infrastructure.llm.lesson_model import (
    WorkspaceLessonModelAdapter,
)

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client
from .test_a05_resource_packs import (
    _evidence_fakes,
    _freeze,
    _freeze_ready_setup,
)
from .test_a03_material_units import _pptx
from .test_a08_pptx_execution import _approved_plan, _sha256
from .test_a11_semester_workspace import _pdf, _semester


def _selection(preflight, link):
    return {
        "material_selections": [
            {
                "link_id": link["link_id"],
                "start_unit": link["start_unit"],
                "end_unit": link["end_unit"],
                **(
                    {"ppt_intent": "keep"}
                    if link["purpose"] == "reference_ppt"
                    else {}
                ),
            }
        ],
        "exercise_candidate_ids": [],
        "question_ids": [],
        "assessment_ids": [],
        "knowledge_scope": [],
        "preparation_preferences": deepcopy(DEFAULT_TEACHING_PREFERENCES),
        "class_name": None,
        "teacher_context": "只允许合成资料范围",
    }


def _suggestion(material, *, material_version_id: str | None = None):
    unit = material["units"][0]
    return {
        "material_version_id": material_version_id
        or material["material_version_id"],
        "question_number": "1",
        "content_label": "合成候选题",
        "difficulty": "medium",
        "classroom_use": "guided_practice",
        "estimated_minutes": 4,
        "teaching_focus": "检查方程变形",
        "reason": "与本节目标直接相关",
        "uncertainties": ["答案区域需教师核对"],
        "question_regions": [
            {
                "material_unit_id": unit["unit_id"],
                "sequence": 1,
                "crop": {"x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.55},
            }
        ],
        "answer_regions": [
            {
                "material_unit_id": unit["unit_id"],
                "sequence": 1,
                "crop": {"x0": 0.1, "y0": 0.58, "x1": 0.9, "y1": 0.9},
            }
        ],
    }


def test_lesson_statuses_are_projected_in_one_semester_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)

    statuses = service.list_lesson_preparation_statuses(semester.id)

    assert [item["lesson_node_id"] for item in statuses] == lesson_ids
    assert all(item["manual_progress"] == "not_started" for item in statuses)
    assert all(item["preparation_stage"] == "select" for item in statuses)
    assert all("latest" in item and "blockers" in item for item in statuses)


def test_lesson_material_readiness_requires_each_parsed_reference_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)
    lesson_id = lesson_ids[0]

    reference, _created = service.register_material_file(
        request_token="a12-readiness-reference",
        path=_pptx(tmp_path / "a12-readiness-reference.pptx"),
        display_name="合成主课件",
    )
    service.parse_material_version(reference.id)
    service.create_material_link(
        request_token="a12-readiness-reference-link",
        lesson_node_id=lesson_id,
        material_version_id=reference.id,
        start_unit=1,
        end_unit=2,
        crop=None,
        purpose="reference_ppt",
        teacher_note=None,
        confirmation_status="confirmed",
    )

    support_versions = []
    for suffix, role in (
        ("textbook", "textbook"),
        ("workbook-a", "exercise_workbook"),
        ("workbook-b", "exercise_workbook"),
    ):
        version, _created = service.register_material_file(
            request_token=f"a12-readiness-{suffix}",
            path=_pdf(tmp_path / f"a12-readiness-{suffix}.pdf", [f"{suffix}-1"]),
            display_name=f"合成{suffix}",
        )
        service.attach_semester_material(
            semester.id,
            request_token=f"a12-readiness-attach-{suffix}",
            material_version_id=version.id,
            material_role=role,
        )
        service.parse_material_version(version.id)
        support_versions.append(version)

    missing = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    assert missing["cells"]["materials"] == {
        "status": "needs_teacher",
        "summary": (
            "待补教材（合成textbook）、"
            "参考教辅（合成workbook-a）、"
            "参考教辅（合成workbook-b）页段"
        ),
        "target_panel": "sources",
    }
    for index, (version, purpose) in enumerate(
        zip(support_versions, ("textbook", "exercise", "exercise"), strict=True),
        start=1,
    ):
        service.create_material_link(
            request_token=f"a12-readiness-support-link-{index}",
            lesson_node_id=lesson_id,
            material_version_id=version.id,
            start_unit=1,
            end_unit=1,
            crop=None,
            purpose=purpose,
            teacher_note=None,
            confirmation_status="confirmed",
        )

    ready = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    assert ready["cells"]["materials"]["status"] == "ready"
    assert ready["cells"]["materials"]["summary"] == "4 份已确认"


def test_exercise_suggestion_normalizes_known_chinese_enum_aliases() -> None:
    material = {
        "purpose": "exercise",
        "material_version_id": "a" * 32,
        "units": [{"unit_id": "b" * 32, "unit_index": 6}],
    }
    raw = _suggestion(material)
    raw["difficulty"] = "中等"
    raw["classroom_use"] = "课堂检测"
    raw["uncertainties"] = ""
    raw["question_regions"][0]["crop"] = [0.1, 0.1, 0.9, 0.55]
    raw["answer_regions"][0]["crop"] = {
        "x0": 0.0,
        "y0": 0.0,
        "x1": 0.0,
        "y1": 0.0,
    }

    normalized = normalize_exercise_suggestion_payload(
        {"suggestions": [raw]}, snapshot={"materials": [material]}
    )

    assert normalized[0]["difficulty"] == "medium"
    assert normalized[0]["classroom_use"] == "diagnostic"
    assert normalized[0]["uncertainties"] == []
    assert normalized[0]["answer_regions"] == []
    assert normalized[0]["question_regions"][0]["crop"] == {
        "x0": 0.1,
        "y0": 0.1,
        "x1": 0.9,
        "y1": 0.55,
    }


def test_exercise_suggestion_accepts_textbook_source_pages() -> None:
    material = {
        "purpose": "textbook",
        "material_version_id": "a" * 32,
        "units": [{"unit_id": "b" * 32, "unit_index": 9}],
    }
    raw = _suggestion(material)
    normalized = normalize_exercise_suggestion_payload(
        {"suggestions": [raw]}, snapshot={"materials": [material]}
    )
    assert normalized[0]["material_version_id"] == "a" * 32


def test_exercise_model_receives_selected_workbook_page_images() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.kwargs = kwargs
            return {"choices": [{"message": {"content": '{"suggestions":[]}'}}]}

    gateway = Gateway()
    adapter = WorkspaceExerciseSuggestionModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )

    assert adapter.generate(
        operation_id="a12-image-request",
        reference_snapshot={
            "materials": [{"purpose": "exercise", "material_version_id": "v1"}],
            "reference_images": [
                {
                    "purpose": "exercise",
                    "material_version_id": "v1",
                    "material_unit_id": "u1",
                    "unit_index": 4,
                    "mime_type": "image/png",
                    "content": b"\x89PNG",
                }
            ],
        },
    ) == {"suggestions": []}
    user_content = gateway.kwargs["kwargs"]["messages"][1]["content"]  # type: ignore[index]
    assert isinstance(user_content, list)
    assert "reference_images" not in user_content[0]["text"]
    assert user_content[1]["text"].startswith("教辅原页；")
    assert user_content[1]["text"].endswith("material_unit_id=u1;unit_index=4")
    assert user_content[2]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


_EMPTY_LESSON_JSON = (
    '{"knowledge_objectives":[],"focus_points":[],'
    '"anticipated_difficulties":[],"lesson_flow":[],'
    '"exercise_recommendations":[],'
    '"slide_adaptations":[],"uncertainties":[]}'
)
_PAGE_LINK_ID = "a" * 32
_PAGE_SOURCE_REF = f"material:{_PAGE_LINK_ID}:unit:1"


def _findings_json(
    citation_ref: str,
    *,
    slide_refs: tuple[str, ...] = (),
    finding: str = "课件整体结构合理",
) -> str:
    import json

    return json.dumps(
        {
            "review_findings": [
                {
                    "slide_refs": list(slide_refs),
                    "finding": finding,
                    "category": "other",
                    "suggested_action": "细看后再定",
                    "citations": [citation_ref],
                }
            ]
        },
        ensure_ascii=False,
    )


def test_lesson_model_sends_compact_catalog_without_preattached_images() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "choices": [
                        {"message": {"content": _findings_json(_PAGE_SOURCE_REF)}}
                    ]
                }
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    payload = adapter.generate(
        operation_id="a12-lesson-catalog-request",
        resource_pack={
            "materials": [
                {
                    "link_id": _PAGE_LINK_ID,
                    "purpose": "textbook",
                    "material_name": "合成教材",
                    "units": [
                        {
                            "unit_id": "b" * 32,
                            "unit_index": 1,
                            "title": "方程",
                            "text": "很长的正文" * 80,
                        }
                    ],
                }
            ],
            "reference_images": [{"content": b"\x89PNG"}],
        },
    )
    assert payload["slide_adaptations"] == []
    first = gateway.calls[0]
    assert first["timeout_override_seconds"] == 300
    assert first["request"].max_physical_calls == 12  # type: ignore[union-attr]
    user_content = first["kwargs"]["messages"][1]["content"]  # type: ignore[index]
    assert isinstance(user_content, str)
    catalog = __import__("json").loads(user_content)
    assert "reference_images" not in catalog
    assert catalog["page_catalog"][0]["source_ref"] == _PAGE_SOURCE_REF
    assert "tools" not in first["kwargs"]
    assert first["kwargs"]["response_format"] == {"type": "json_object"}  # type: ignore[index]
    second = gateway.calls[1]
    assert "tools" in second["kwargs"]  # type: ignore[index]
    assert "response_format" not in second["kwargs"]  # type: ignore[index]


def _tiny_png(width: int = 1600, height: int = 900) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    image = Image.effect_noise((width, height), 100).convert("RGB")
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_lesson_model_first_round_attaches_all_slide_thumbnails() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "choices": [
                        {"message": {"content": _findings_json(_PAGE_SOURCE_REF)}}
                    ]
                }
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    def page_loader(source_ref: str) -> dict[str, object]:
        unit_index = int(source_ref.rsplit(":unit:", 1)[1])
        return {
            "ok": True,
            "source_ref": source_ref,
            "purpose": "reference_ppt",
            "unit_index": unit_index,
            "unit_id": f"{unit_index:0>32}",
            "label": f"主课件 第 {unit_index} 页",
            "mime_type": "image/png",
            "content": _tiny_png(),
            "preview_url": None,
        }

    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    adapter.generate(
        operation_id="a12-first-round-thumbnails",
        resource_pack={
            "materials": [
                {
                    "link_id": _PAGE_LINK_ID,
                    "purpose": "reference_ppt",
                    "material_name": "合成课件",
                    "material_version_id": "v1",
                    "units": [
                        {
                            "unit_id": f"{index:0>32}",
                            "unit_index": index,
                            "title": f"合成页 {index}",
                        }
                        for index in range(1, 4)
                    ],
                },
                {
                    "link_id": "c" * 32,
                    "purpose": "textbook",
                    "material_name": "合成教材",
                    "units": [
                        {"unit_id": "d" * 32, "unit_index": 10, "title": "教材页"}
                    ],
                },
            ],
        },
        page_loader=page_loader,
    )
    user_content = gateway.calls[0]["kwargs"]["messages"][1]["content"]  # type: ignore[index]
    assert isinstance(user_content, list)
    catalog = __import__("json").loads(user_content[0]["text"])
    assert catalog["first_round_slide_images"]["attached"] == 3
    image_parts = [
        part for part in user_content if part.get("type") == "image_url"
    ]
    assert len(image_parts) == 3
    labels = [
        part["text"]
        for part in user_content
        if part.get("type") == "text" and "unit_index=" in part["text"]
    ]
    assert all(label.startswith("主课件原页；") for label in labels)
    assert [label.rsplit("unit_index=", 1)[1] for label in labels] == [
        "1",
        "2",
        "3",
    ]
    import base64
    import io

    from PIL import Image

    for part in image_parts:
        url = part["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        data = base64.b64decode(url.split(",", 1)[1])
        with Image.open(io.BytesIO(data)) as image:
            assert max(image.size) <= 480


def test_lesson_model_first_round_thumbnails_capped_at_forty_slides() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "choices": [
                        {"message": {"content": _findings_json(_PAGE_SOURCE_REF)}}
                    ]
                }
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    def page_loader(source_ref: str) -> dict[str, object]:
        unit_index = int(source_ref.rsplit(":unit:", 1)[1])
        return {
            "ok": True,
            "source_ref": source_ref,
            "purpose": "reference_ppt",
            "unit_index": unit_index,
            "unit_id": f"{unit_index:0>32}",
            "label": f"主课件 第 {unit_index} 页",
            "mime_type": "image/png",
            "content": _tiny_png(120, 90),
            "preview_url": None,
        }

    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    adapter.generate(
        operation_id="a12-first-round-thumbnail-cap",
        resource_pack={
            "materials": [
                {
                    "link_id": _PAGE_LINK_ID,
                    "purpose": "reference_ppt",
                    "material_name": "合成课件",
                    "units": [
                        {"unit_id": f"{index:0>32}", "unit_index": index}
                        for index in range(1, 46)
                    ],
                }
            ],
        },
        page_loader=page_loader,
    )
    user_content = gateway.calls[0]["kwargs"]["messages"][1]["content"]  # type: ignore[index]
    assert isinstance(user_content, list)
    catalog = __import__("json").loads(user_content[0]["text"])
    assert catalog["first_round_slide_images"]["attached"] == 40
    image_parts = [
        part for part in user_content if part.get("type") == "image_url"
    ]
    assert len(image_parts) == 40


def test_lesson_model_fetches_pages_then_returns_json() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": _findings_json("lesson:a12-lesson-1")
                            }
                        }
                    ]
                }
            if len(self.calls) == 2:
                return {
                    "choices": [
                        {
                            "message": {
                                "reasoning_content": "先看教材第 1 页",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "get_frozen_page",
                                            "arguments": (
                                                '{"source_ref":"%s"}'
                                                % _PAGE_SOURCE_REF
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    loaded: list[str] = []
    events: list[dict[str, object]] = []

    def page_loader(source_ref: str) -> dict[str, object]:
        loaded.append(source_ref)
        return {
            "ok": True,
            "source_ref": source_ref,
            "purpose": "textbook",
            "unit_index": 1,
            "unit_id": "b" * 32,
            "label": "教材 第 1 页",
            "mime_type": "image/png",
            "content": b"\x89PNG",
            "preview_url": "/api/teaching-prep/material-units/%s/preview"
            % ("b" * 32),
        }

    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    payload = adapter.generate(
        operation_id="a12-lesson-tool-loop",
        resource_pack={
            "lesson": {"lesson_node_id": "a12-lesson-1", "title": "合成课"},
            "materials": [],
        },
        page_loader=page_loader,
        observer=events.append,
    )
    assert payload["slide_adaptations"] == []
    assert loaded == [_PAGE_SOURCE_REF]
    assert len(gateway.calls) == 3
    first_kwargs = gateway.calls[0]["kwargs"]
    assert "tools" not in first_kwargs
    assert first_kwargs["response_format"] == {"type": "json_object"}
    second_kwargs = gateway.calls[1]["kwargs"]
    assert "tools" in second_kwargs
    assert "response_format" not in second_kwargs
    third_kwargs = gateway.calls[2]["kwargs"]
    assert "tools" in third_kwargs
    third_messages = third_kwargs["messages"]
    assert any(message.get("role") == "tool" for message in third_messages)
    image_message = third_messages[-1]
    assert image_message["role"] == "user"
    assert any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in image_message["content"]
    )
    assert any(item.get("phase") == "thinking" for item in events)
    findings_events = [
        item for item in events if item.get("phase") == "findings_ready"
    ]
    assert len(findings_events) == 1
    assert findings_events[0]["round"] == 1
    assert findings_events[0]["findings"] == [
        {
            "finding": "课件整体结构合理",
            "category": "other",
            "pages": [],
        }
    ]
    assert any(item.get("phase") == "tool_call" for item in events)
    assert any(item.get("phase") == "tool_result" for item in events)
    assert any("取页 · 教材 第 1 页" == item.get("summary") for item in events)
    assert any("已返回教材 第 1 页" == item.get("summary") for item in events)
    assert events[-1]["phase"] == "final_accepted"


def test_lesson_model_rejects_invalid_and_excess_page_tools_without_loader() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": _findings_json("lesson:a12-lesson-1")
                            }
                        }
                    ]
                }
            if len(self.calls) == 2:
                calls = [
                    {
                        "id": f"call-{index}",
                        "type": "function",
                        "function": {
                            "name": "get_frozen_page",
                            "arguments": '{"source_ref":"C:\\\\secret\\\\page.png"}',
                        },
                    }
                    for index in range(5)
                ]
                return {"choices": [{"message": {"tool_calls": calls}}]}
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    loaded: list[str] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    adapter.generate(
        operation_id="a12-lesson-reject-tools",
        resource_pack={
            "lesson": {"lesson_node_id": "a12-lesson-1", "title": "合成课"},
            "materials": [],
        },
        page_loader=lambda source_ref: loaded.append(source_ref) or {},
    )
    assert loaded == []
    tool_messages = [
        message
        for message in gateway.calls[2]["kwargs"]["messages"]  # type: ignore[index]
        if message.get("role") == "tool"
    ]
    assert len(tool_messages) == 5
    assert "not an allowed page_catalog value" in tool_messages[0]["content"]
    assert "already used 4 pages" in tool_messages[4]["content"]


def test_lesson_model_retries_recoverable_error_once_per_round() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise TimeoutError("read timed out")
            if len(self.calls) == 2:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": _findings_json("lesson:a12-lesson-1")
                            }
                        }
                    ]
                }
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    events: list[dict[str, object]] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    payload = adapter.generate(
        operation_id="a12-lesson-retry-once",
        resource_pack={
            "lesson": {"lesson_node_id": "a12-lesson-1", "title": "合成课"},
            "materials": [],
        },
        observer=events.append,
    )
    assert payload["slide_adaptations"] == []
    assert len(gateway.calls) == 3
    assert all(
        call["timeout_override_seconds"] == 300 for call in gateway.calls
    )
    retry_events = [item for item in events if item.get("phase") == "round_retry"]
    assert len(retry_events) == 1
    assert retry_events[0]["round"] == 1
    assert "timeout" in str(retry_events[0]["summary"])
    assert events[-1]["phase"] == "final_accepted"


def test_lesson_model_raises_when_retry_also_times_out() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            raise TimeoutError("read timed out")

    events: list[dict[str, object]] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    with pytest.raises(TimeoutError):
        adapter.generate(
            operation_id="a12-lesson-retry-exhausted",
            resource_pack={"materials": []},
            observer=events.append,
        )
    assert len(gateway.calls) == 2
    retry_events = [item for item in events if item.get("phase") == "round_retry"]
    assert len(retry_events) == 1


def test_lesson_model_does_not_retry_client_errors() -> None:
    class _ClientError(Exception):
        status_code = 401

    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            raise _ClientError("unauthorized")

    events: list[dict[str, object]] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    with pytest.raises(_ClientError):
        adapter.generate(
            operation_id="a12-lesson-no-retry-4xx",
            resource_pack={"materials": []},
            observer=events.append,
        )
    assert len(gateway.calls) == 1
    assert not any(item.get("phase") == "round_retry" for item in events)


def test_lesson_model_reprompts_invalid_findings_within_round_one() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}
            if len(self.calls) == 2:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": _findings_json("lesson:a12-lesson-1")
                            }
                        }
                    ]
                }
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    events: list[dict[str, object]] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    payload = adapter.generate(
        operation_id="a12-lesson-findings-reprompt",
        resource_pack={
            "lesson": {"lesson_node_id": "a12-lesson-1", "title": "合成课"},
            "materials": [],
        },
        observer=events.append,
    )
    assert payload["slide_adaptations"] == []
    assert len(gateway.calls) == 3
    for call in gateway.calls[:2]:
        assert "tools" not in call["kwargs"]
        assert call["kwargs"]["response_format"] == {"type": "json_object"}  # type: ignore[index]
    reprompts = [
        message
        for message in gateway.calls[1]["kwargs"]["messages"]  # type: ignore[index]
        if message.get("role") == "user"
        and "第 1 轮只接受初步审课发现" in str(message.get("content"))
    ]
    assert len(reprompts) == 1
    assert "tools" in gateway.calls[2]["kwargs"]  # type: ignore[index]
    findings_events = [
        item for item in events if item.get("phase") == "findings_ready"
    ]
    assert len(findings_events) == 1
    assert findings_events[0]["round"] == 1
    assert events[-1]["phase"] == "final_accepted"


def test_lesson_model_fails_when_findings_stay_invalid() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    events: list[dict[str, object]] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    with pytest.raises(TeachingPrepValidationError):
        adapter.generate(
            operation_id="a12-lesson-findings-invalid",
            resource_pack={
                "lesson": {"lesson_node_id": "a12-lesson-1", "title": "合成课"},
                "materials": [],
            },
            observer=events.append,
        )
    assert len(gateway.calls) == 2
    assert not any(item.get("phase") == "findings_ready" for item in events)
    assert events[-1]["phase"] == "failed"
    assert events[-1]["round"] == 1


def test_lesson_model_findings_ready_maps_pages_and_truncates() -> None:
    long_finding = "练习量偏大" * 60
    findings_payload = __import__("json").dumps(
        {
            "review_findings": [
                {
                    "slide_refs": [
                        f"material:{_PAGE_LINK_ID}:unit:2",
                        f"material:{_PAGE_LINK_ID}:unit:5",
                    ],
                    "finding": long_finding,
                    "category": "practice_load",
                    "suggested_action": "细看后删减",
                    "citations": [f"material:{_PAGE_LINK_ID}:unit:2"],
                },
                {
                    "slide_refs": [],
                    "finding": "整体顺序合理",
                    "category": "sequence",
                    "suggested_action": "保持",
                    "citations": [_PAGE_SOURCE_REF],
                },
            ]
            + [
                {
                    "slide_refs": [],
                    "finding": f"补充发现 {index}",
                    "category": "other",
                    "suggested_action": "保持",
                    "citations": [_PAGE_SOURCE_REF],
                }
                for index in range(25)
            ]
        },
        ensure_ascii=False,
    )

    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {"choices": [{"message": {"content": findings_payload}}]}
            return {"choices": [{"message": {"content": _EMPTY_LESSON_JSON}}]}

    events: list[dict[str, object]] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    adapter.generate(
        operation_id="a12-lesson-findings-trace",
        resource_pack={
            "materials": [
                {
                    "link_id": _PAGE_LINK_ID,
                    "purpose": "reference_ppt",
                    "material_name": "合成课件",
                    "units": [
                        {"unit_id": f"{index:0>32}", "unit_index": index}
                        for index in range(1, 6)
                    ],
                }
            ],
        },
        observer=events.append,
    )
    findings_events = [
        item for item in events if item.get("phase") == "findings_ready"
    ]
    assert len(findings_events) == 1
    event = findings_events[0]
    assert event["round"] == 1
    assert "27 条" in str(event["summary"])
    findings = event["findings"]
    assert len(findings) == 20
    assert findings[0]["pages"] == [2, 5]
    assert findings[0]["category"] == "practice_load"
    assert len(findings[0]["finding"]) == 240
    assert str(findings[0]["finding"]).endswith("…")
    assert findings[1]["pages"] == []


def test_lesson_model_final_review_findings_replace_preliminary() -> None:
    final_json = __import__("json").dumps(
        {
            "review_findings": [
                {
                    "slide_refs": [],
                    "finding": "细看后修正：练习量其实合适",
                    "category": "practice_load",
                    "suggested_action": "保持",
                    "citations": ["lesson:a12-lesson-1"],
                }
            ]
        },
        ensure_ascii=False,
    )

    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_completions(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": _findings_json(
                                    "lesson:a12-lesson-1",
                                    finding="初步判断：练习量偏大",
                                )
                            }
                        }
                    ]
                }
            return {"choices": [{"message": {"content": final_json}}]}

    events: list[dict[str, object]] = []
    gateway = Gateway()
    adapter = WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )
    payload = adapter.generate(
        operation_id="a12-lesson-findings-revised",
        resource_pack={
            "lesson": {"lesson_node_id": "a12-lesson-1", "title": "合成课"},
            "materials": [],
        },
        observer=events.append,
    )
    assert payload["review_findings"][0]["finding"] == "细看后修正：练习量其实合适"
    findings_events = [
        item for item in events if item.get("phase") == "findings_ready"
    ]
    assert findings_events[0]["findings"][0]["finding"] == "初步判断：练习量偏大"
    assert events[-1]["phase"] == "final_accepted"


def test_load_frozen_page_for_model_rejects_path_like_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    result = service.load_frozen_page_for_model(
        {"materials": []},
        r"C:\secret\page.png",
    )
    assert result["ok"] is False
    assert "content" not in result
    assert "C:" not in str(result.get("error") or "")


def test_adaptation_trace_http_returns_teacher_safe_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    operation_id = "a12-trace-operation-01"
    unit_id = "b" * 32
    source_ref = f"material:{'a' * 32}:unit:1"
    service.adaptation_traces.append_event(
        lesson_node_id=lesson_id,
        operation_id=operation_id,
        event={
            "round": 1,
            "phase": "thinking",
            "summary": "模型正在分析本课目录和已取原页。",
            "thinking_excerpt": r"先看教材第 1 页 C:\secret\page.png",
            "tool": None,
            "result": None,
            "model_calls_used": 1,
            "model_calls_max": 6,
        },
    )
    service.adaptation_traces.append_event(
        lesson_node_id=lesson_id,
        operation_id=operation_id,
        event={
            "round": 1,
            "phase": "tool_call",
            "summary": "取页 · 教材 第 1 页",
            "thinking_excerpt": None,
            "tool": {
                "name": "get_frozen_page",
                "purpose": "textbook",
                "page": 1,
                "source_ref": source_ref,
            },
            "result": None,
            "model_calls_used": 1,
            "model_calls_max": 6,
        },
    )
    service.adaptation_traces.append_event(
        lesson_node_id=lesson_id,
        operation_id=operation_id,
        event={
            "round": 1,
            "phase": "tool_result",
            "summary": "已返回教材 第 1 页",
            "thinking_excerpt": None,
            "tool": {
                "name": "get_frozen_page",
                "purpose": "textbook",
                "page": 1,
                "source_ref": r"C:\secret\page.png",
            },
            "result": {
                "ok": True,
                "label": "教材 第 1 页",
                "preview_url": f"/api/teaching-prep/material-units/{unit_id}/preview",
            },
            "model_calls_used": 2,
            "model_calls_max": 6,
        },
    )
    service.adaptation_traces.append_event(
        lesson_node_id=lesson_id,
        operation_id=operation_id,
        event={
            "round": 1,
            "phase": "findings_ready",
            "summary": "已给出初步审课发现 1 条（细看后可能修正）",
            "thinking_excerpt": None,
            "tool": None,
            "result": None,
            "findings": [
                {
                    "finding": r"第 3 页练习重复 https://x.example C:\secret\page.png",
                    "category": "practice_load",
                    "pages": [3, -1, "x", 5],
                },
                {
                    "finding": "   ",
                    "category": "other",
                    "pages": [1],
                },
                "not-a-mapping",
            ],
            "model_calls_used": 2,
            "model_calls_max": 6,
        },
    )
    client = _api_client(service)
    missing = client.get(
        f"/api/teaching-prep/lessons/{'0' * 32}/adaptation-trace"
    )
    assert missing.status_code == 404
    response = client.get(
        f"/api/teaching-prep/lessons/{lesson_id}/adaptation-trace",
        params={"operation_id": operation_id},
    )
    assert response.status_code == 200
    payload = response.json()
    dumped = str(payload)
    assert "C:" not in dumped
    assert "secret" not in dumped
    assert payload["status"] == "running"
    assert payload["model_calls_used"] == 2
    assert payload["model_calls_max"] == 6
    assert payload["events"][0]["thinking_excerpt"] == (
        "先看教材第 1 页 [LOCAL_PATH_REDACTED]"
    )
    assert payload["events"][0]["findings"] is None
    assert payload["events"][1]["summary"] == "取页 · 教材 第 1 页"
    assert payload["events"][1]["tool"]["source_ref"] == source_ref
    assert payload["events"][2]["tool"]["source_ref"] == ""
    assert payload["events"][2]["result"]["preview_url"] == (
        f"/api/teaching-prep/material-units/{unit_id}/preview"
    )
    findings_event = payload["events"][3]
    assert findings_event["phase"] == "findings_ready"
    assert findings_event["findings"] == [
        {
            "finding": (
                "第 3 页练习重复 [URL_REDACTED] [LOCAL_PATH_REDACTED]"
            ),
            "category": "practice_load",
            "pages": [3, 5],
        }
    ]


def test_select_model_page_images_round_robins_and_caps_total() -> None:
    from backend.teaching_prep.application.preparation_service import (
        _select_model_page_images,
    )

    buckets = {
        "exercise": [
            {"purpose": "exercise", "material_unit_id": f"e{index}", "content": b"e"}
            for index in range(3)
        ],
        "textbook": [
            {"purpose": "textbook", "material_unit_id": f"t{index}", "content": b"t"}
            for index in range(8)
        ],
        "reference_ppt": [
            {
                "purpose": "reference_ppt",
                "material_unit_id": f"p{index}",
                "content": b"p",
            }
            for index in range(9)
        ],
    }
    selected = _select_model_page_images(
        buckets,
        purposes=("exercise", "textbook"),
        max_images=12,
        max_bytes=12_000_000,
    )
    purposes = [str(item["purpose"]) for item in selected]
    assert purposes == (
        ["exercise", "textbook"] * 3
        + ["textbook"] * 5
    )
    assert "reference_ppt" not in purposes
    assert len(selected) == 11


def test_preview_payload_for_model_compresses_large_png(tmp_path: Path) -> None:
    from backend.teaching_prep.application.preparation_service import (
        _preview_payload_for_model,
    )
    from PIL import Image

    preview = tmp_path / "large-slide.png"
    Image.frombytes("RGB", (1600, 900), os.urandom(1600 * 900 * 3)).save(preview)
    mime_type, content = _preview_payload_for_model(preview)
    assert mime_type == "image/jpeg"
    assert content.startswith(b"\xff\xd8")
    assert len(content) < preview.stat().st_size


def _add_exercise_link(service, lesson_id: str, *, token: str):
    preflight = service.reference_selection_preflight(lesson_id)
    source = next(
        item
        for item in preflight["catalog"]["material_links"]
        if item["purpose"] == "textbook"
    )
    service.create_material_link(
        request_token=token,
        lesson_node_id=lesson_id,
        material_version_id=source["material_version_id"],
        start_unit=source["start_unit"],
        end_unit=source["end_unit"],
        crop=None,
        purpose="exercise",
        teacher_note="合成普通教辅范围",
        confirmation_status="confirmed",
    )


def test_latest_material_source_version_marks_dependent_home_cells_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    curriculum = service.list_curricula()[0]
    semester, _created = service.create_semester(
        request_token="a12-stale-semester",
        curriculum_id=curriculum.id,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )
    pack, _created = _freeze(
        service,
        token="a12-stale-pack",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )
    service.generate_lesson_draft(
        pack.id,
        operation_id="a12-stale-local-draft",
        mode="local_template",
        confirmed=True,
    )
    before = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    old_version = service.get_material_version(reference_link.material_version_id)
    replacement_path = _pptx(tmp_path / "a12-reference-v2.pptx")
    with zipfile.ZipFile(replacement_path, "a") as archive:
        archive.writestr("docProps/a12-version.txt", "synthetic-v2")
    replacement, created = service.register_material_file(
        request_token="a12-reference-v2",
        path=replacement_path,
        display_name=old_version.display_name,
        source_id=old_version.source_id,
    )

    assert created is True
    assert replacement.id != old_version.id
    assert service.resource_pack_status(lesson_id)["local_sources_changed"] is True
    after = next(
        item for item in service.list_lesson_preparation_statuses(semester.id)
        if item["lesson_node_id"] == lesson_id
    )
    assert after["cells"]["materials"]["status"] == "stale"
    assert after["cells"]["plan"]["status"] == "stale"
    assert after["cells"]["exercises"]["status"] == "stale"
    assert after["next_action"] == "重新核对资料"
    assert after["summary_revision"] != before["summary_revision"]


def test_lesson_statuses_include_only_actionable_public_ai_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    semester, lesson_ids = _semester(service)
    lesson_id = lesson_ids[0]

    def task(task_id: str, status: str, adoption_state: str | None = None):
        handoffs = () if adoption_state is None else (
            SimpleNamespace(
                adoption_state=adoption_state,
                subject_refs=(SimpleNamespace(kind="lesson", id=lesson_id),),
            ),
        )
        return SimpleNamespace(
            task_id=task_id,
            module="teaching_prep",
            task_kind="teaching_prep.lesson_plan",
            status=status,
            source_ref=SimpleNamespace(kind="lesson", id=lesson_id),
            proposal_ref_id=(None if status in {"queued", "running"} else f"proposal-{task_id}"),
            proposal_revision=(None if status in {"queued", "running"} else "1"),
            pending_count=(1 if adoption_state in {"pending", "opened", "adoption_started"} else 0),
            handoffs=handoffs,
        )

    statuses = service.list_lesson_preparation_statuses(
        semester.id,
        ai_tasks=(
            task("queued-task", "queued"),
            task("pending-proposal", "proposal_ready", "pending"),
            task("adopted-proposal", "proposal_ready", "adopted"),
            task("discarded-proposal", "proposal_ready", "discarded"),
        ),
    )
    lesson = next(item for item in statuses if item["lesson_node_id"] == lesson_id)

    assert [item["task_id"] for item in lesson["ai_tasks"]] == [
        "queued-task",
        "pending-proposal",
    ]
    assert lesson["ai_tasks"][0]["proposal_ref_id"] is None


def test_reference_snapshot_limits_model_input_and_review_never_verifies_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    _add_exercise_link(service, lesson_id, token="a12-exercise-link")
    preflight = service.reference_selection_preflight(lesson_id)
    selected = next(
        item for item in preflight["catalog"]["material_links"]
        if item["purpose"] == "exercise"
    )
    outside = next(
        item for item in preflight["catalog"]["material_links"]
        if item["material_version_id"] != selected["material_version_id"]
    )
    draft = service.save_reference_selection_draft(
        lesson_id,
        expected_revision=None,
        source_state_sha256=preflight["source_state_sha256"],
        selection=_selection(preflight, selected),
    )
    snapshot, created = service.freeze_reference_selection_snapshot(
        lesson_id,
        request_token="a12-reference-snapshot-0001",
        expected_draft_revision=draft.revision,
    )
    assert created is True
    assert [
        item["material_version_id"]
        for item in snapshot.payload["model_input"]["materials"]
    ] == [selected["material_version_id"]]

    adapter = FakeExerciseSuggestionModelAdapter(
        {"suggestions": [_suggestion(selected)]}
    )
    service.exercise_suggestion_model_adapter = adapter
    run, run_created = service.start_exercise_suggestion_run(
        snapshot.id,
        operation_id="a12-suggestion-operation-0001",
        confirmed=True,
    )
    repeated, repeated_created = service.start_exercise_suggestion_run(
        snapshot.id,
        operation_id="a12-suggestion-operation-0001",
        confirmed=True,
    )
    assert run_created is True
    assert repeated_created is False
    assert repeated.id == run.id

    service.process_exercise_suggestion_run(run.id)
    finished, suggestions = service.get_exercise_suggestion_run(run.id)
    assert finished.status == "succeeded"
    assert finished.model_call_count == 1
    assert len(adapter.calls) == 1
    reference_images = adapter.calls[0]["reference_snapshot"]["reference_images"]
    assert [item["material_unit_id"] for item in reference_images] == [
        unit["unit_id"] for unit in selected["units"]
    ]
    assert all(item["content"].startswith(b"\x89PNG") for item in reference_images)
    accepted = service.review_exercise_suggestion(
        suggestions[0].id,
        expected_revision=suggestions[0].revision,
        decision="accepted",
        teacher_payload=None,
        rejection_reason=None,
    )
    candidate = next(
        item
        for item in service.list_exercise_candidates(lesson_id)
        if item.id == accepted.exercise_candidate_id
    )
    assert candidate.answer_status == "candidate"
    assert candidate.answer_status != "teacher_verified"

    second_snapshot, _created = service.freeze_reference_selection_snapshot(
        lesson_id,
        request_token="a12-reference-snapshot-0002",
        expected_draft_revision=draft.revision,
    )
    service.exercise_suggestion_model_adapter = FakeExerciseSuggestionModelAdapter(
        {
            "suggestions": [
                _suggestion(
                    selected,
                    material_version_id=outside["material_version_id"],
                )
            ]
        }
    )
    invalid, _created = service.start_exercise_suggestion_run(
        second_snapshot.id,
        operation_id="a12-suggestion-operation-outside",
        confirmed=True,
    )
    service.process_exercise_suggestion_run(invalid.id)
    invalid_result, invalid_items = service.get_exercise_suggestion_run(invalid.id)
    assert invalid_result.status == "failed"
    assert invalid_items == ()


def test_resource_subset_and_capacity_preview_have_no_hidden_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, candidate = _freeze_ready_setup(service, tmp_path)
    all_links = service.list_material_links(lesson_id)

    preflight = service.resource_pack_preflight(
        lesson_id,
        reference_ppt_intents={reference_link.id: "keep"},
        selected_material_link_ids=[item.id for item in all_links],
        selected_exercise_candidate_ids=[candidate.id],
    )
    assert preflight["ready_to_freeze"] is True

    pack, _created = service.freeze_resource_pack(
        request_token="a12-resource-subset-0001",
        lesson_node_id=lesson_id,
        class_name=None,
        lesson_type="new_lesson",
        teacher_context=None,
        reference_ppt_intents={reference_link.id: "keep"},
        question_ids=[],
        assessment_ids=[],
        knowledge_scope=[],
        selected_material_link_ids=[item.id for item in all_links],
        selected_exercise_candidate_ids=[candidate.id],
    )
    assert pack.payload["selection"]["material_link_ids"] == [
        item.id for item in all_links
    ]
    assert pack.payload["selection"]["exercise_candidate_ids"] == [candidate.id]

    local, _created = service.generate_lesson_draft(
        pack.id,
        operation_id="a12-local-draft-operation",
        mode="local_template",
        confirmed=True,
    )
    before = service.list_lesson_drafts(pack.id)
    capacity = service.preview_lesson_draft_capacity(local.id, payload=local.payload)
    after = service.list_lesson_drafts(pack.id)
    assert capacity["lesson_minutes"] > 0
    assert [item.id for item in after] == [item.id for item in before]


def test_async_pptx_start_publishes_trusted_current_version_without_touching_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    plan = _approved_plan(service, tmp_path)
    source = tmp_path / "a05-reference.pptx"
    source_before = _sha256(source)
    service.wps_adapter = FakeWpsAdapter()

    started, created = service.start_pptx_execution(
        plan.id,
        operation_id="a12-async-pptx-operation",
        confirmed=True,
    )
    assert created is True
    assert started.status == "running"
    assert started.phase == "copying"

    service.process_pptx_execution(started.id)
    finished = service.get_pptx_execution(started.id)
    versions = service.list_lesson_pptx_versions(
        service.get_resource_pack(plan.resource_pack_id).lesson_node_id
    )
    assert finished.status == "published", (
        finished.error_code,
        finished.execution_report,
        finished.verification_report,
    )
    assert finished.phase == "done"
    assert len(versions) == 1
    assert versions[0]["is_current"] is True
    assert versions[0]["file_verified"] is True
    assert service.pptx_version_preview_path(
        versions[0]["version"].id
    ).is_file()
    assert _sha256(source) == source_before

    version = versions[0]["version"]
    same, revision, changed = service.activate_pptx_version(
        version.id,
        expected_revision=versions[0]["current_revision"],
    )
    assert same.id == version.id
    assert changed is False
    assert revision == versions[0]["current_revision"]
    with pytest.raises(TeachingPrepConflictError):
        service.activate_pptx_version(version.id, expected_revision=revision + 1)


def test_workbench_http_contract_runs_suggestions_as_an_observable_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _reference_link, _candidate = _freeze_ready_setup(service, tmp_path)
    _add_exercise_link(service, lesson_id, token="a12-http-exercise-link")
    preflight = service.reference_selection_preflight(lesson_id)
    selected = next(
        item for item in preflight["catalog"]["material_links"]
        if item["purpose"] == "exercise"
    )
    service.exercise_suggestion_model_adapter = FakeExerciseSuggestionModelAdapter(
        {"suggestions": [_suggestion(selected)]}
    )
    client = _api_client(service)

    response = client.put(
        f"/api/teaching-prep/lessons/{lesson_id}/reference-selection-draft",
        json={
            "expected_revision": None,
            "source_state_sha256": preflight["source_state_sha256"],
            "selection": _selection(preflight, selected),
        },
    )
    assert response.status_code == 200
    draft = response.json()
    response = client.post(
        f"/api/teaching-prep/lessons/{lesson_id}/reference-selection-snapshots",
        json={
            "request_token": "a12-http-snapshot-0001",
            "expected_draft_revision": draft["revision"],
        },
    )
    assert response.status_code == 201
    snapshot = response.json()
    response = client.post(
        f"/api/teaching-prep/reference-selection-snapshots/{snapshot['id']}/exercise-suggestion-runs",
        json={
            "operation_id": "a12-http-suggestion-operation",
            "confirmed": True,
        },
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    finished = client.get(
        f"/api/teaching-prep/exercise-suggestion-runs/{run_id}"
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "succeeded"
    assert len(finished.json()["suggestions"]) == 1

    missing = client.get(
        f"/api/teaching-prep/lessons/{'0' * 32}/latest-exercise-suggestion-run"
    )
    assert missing.status_code == 404
    latest = client.get(
        f"/api/teaching-prep/lessons/{lesson_id}/latest-exercise-suggestion-run"
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == run_id
    assert latest.json()["status"] == "succeeded"
    assert len(latest.json()["suggestions"]) == 1
