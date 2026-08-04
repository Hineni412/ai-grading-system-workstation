from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

from backend.llm.json_repair import parse_json_object_locally
from backend.llm.usage import response_diagnostics
from backend.teaching_prep.application.semester_mapping_evidence import (
    build_directory_evidence,
)
from backend.teaching_prep.domain.errors import (
    TeachingPrepModelResponseError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.infrastructure.llm.lesson_model import (
    _response_text,
)
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)


_MAX_OUTPUT_TOKENS = 12_288
_SYSTEM_INSTRUCTION = """\
你是初中数学学期资料目录整理助手。只依据给出的学期快照工作。
返回单个紧凑 JSON 对象，不要 Markdown、代码围栏、解释或缩进，只能包含
tree、mappings、uncertainties。严格使用下面的字段结构：
{"tree":[{"key":"chapter_1","title":"章名","sections":[{"key":"section_1",
"title":"节名","lessons":[{"key":"lesson_1","title":"课时名",
"duration_minutes":45}]}]}],"mappings":[{"material_record_id":"原样复制资料ID",
"lesson_ref":"proposal:lesson_1","start_unit":1,"end_unit":2,
"basis":"依据目录与正文标题推断","evidence_refs":["toc-001","anchor-0012"]}],
"uncertainties":[]}
所有 key 必须在整个 tree 全局唯一；推荐使用 chapter_01、
chapter_01_section_01、chapter_01_section_01_lesson_01 这种带完整层级的 key。
如果快照已经有课时树，tree 必须为空，只把新增资料映射到 existing lesson id。
如果课时树为空，tree 按章、节、课时三级给出，每个节点使用简短唯一 key；
sections 只表示“节”，课时必须放入 lessons；禁止空 lessons。每个课时必须包含
duration_minutes（1—300 的整数），通常使用 45。尽量贴近 planned_new_lesson_count；
证据不足时减少课时并说明 uncertainty，不要用空数组占位。
mapping 的 lesson_ref 对新课时使用 proposal:<lesson key>，只能引用 lesson 的 key，
不能引用 chapter 或 section 的 key。连续页段合并，不要为每页重复建立 mapping。
每条 mapping 必须包含 material_record_id、lesson_ref、start_unit、end_unit、basis、
evidence_refs。basis 用一句短话说明依据；evidence_refs 只能引用 directory_evidence 中
真实存在的 evidence_id，不能编造。相邻且属于同一课时的页必须合并成一个连续页段。
页码必须是快照中真实 unit_index 范围，不得编造页码、课时或资料。
不能确定时写入 uncertainties，不要猜测。不要返回题目正文、答案或 WPS 指令。
"""


class WorkspaceSemesterMappingModelAdapter:
    """Single-call semester mapping seam; caller controls real-call authority."""

    def __init__(
        self,
        *,
        gateway: WorkspaceModelGateway,
        client: object,
        model: str,
    ) -> None:
        self.gateway = gateway
        self.client = client
        self.model = str(model or "").strip()
        if not self.model:
            raise TeachingPrepValidationError(
                "semester mapping model is required"
            )

    def generate(
        self,
        *,
        operation_id: str,
        semester_snapshot: dict[str, Any],
        dispatch_callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        client = _client_with_dispatch_callback(
            self.client,
            dispatch_callback,
        )
        response = self.gateway.chat_completions(
            request=WorkspaceModelRequest(
                purpose="semester_mapping",
                data_classification="teaching_material_aggregate",
                operation_id=operation_id,
            ),
            client=client,
            model=self.model,
            kwargs={
                "messages": [
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {
                        "role": "user",
                        "content": json.dumps(
                            _compact_model_snapshot(semester_snapshot),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": _MAX_OUTPUT_TOKENS,
            },
            timeout_override_seconds=600,
        )
        diagnostics = response_diagnostics(response)
        if bool(diagnostics.get("output_truncated")):
            raise TeachingPrepModelResponseError(
                "semester mapping model output was truncated",
                error_code="semester_mapping_model_response_truncated",
            )
        try:
            response_text = _response_text(response)
        except TeachingPrepValidationError as exc:
            raise TeachingPrepModelResponseError(
                "semester mapping model response text is unavailable",
                error_code=(
                    "semester_mapping_model_response_text_unavailable"
                ),
            ) from exc
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            try:
                payload = parse_json_object_locally(response_text).payload
            except (TypeError, ValueError):
                raise TeachingPrepModelResponseError(
                    "semester mapping model returned invalid JSON",
                    error_code="semester_mapping_model_response_invalid_json",
                ) from exc
        if not isinstance(payload, Mapping):
            raise TeachingPrepModelResponseError(
                "semester mapping model response must be an object",
                error_code="semester_mapping_model_response_invalid_type",
            )
        return dict(payload)


def _compact_model_snapshot(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    directory_evidence = snapshot.get("directory_evidence")
    if not isinstance(directory_evidence, Mapping):
        directory_evidence = build_directory_evidence(snapshot)
    semester = _selected_fields(
        snapshot.get("semester"),
        (
            "school_year",
            "term",
            "planned_new_lesson_count",
            "curriculum_title",
        ),
    )
    lessons = [
        _selected_fields(
            item,
            (
                "id",
                "parent_id",
                "node_type",
                "title",
                "sort_order",
                "duration_minutes",
            ),
        )
        for item in _mapping_list(snapshot.get("lessons"))
    ]
    materials: list[dict[str, object]] = []
    for item in _mapping_list(snapshot.get("materials")):
        material = _selected_fields(
            item,
            (
                "record_id",
                "display_name",
                "material_role",
                "unit_count",
            ),
        )
        materials.append(material)
    return {
        "semester": semester,
        "lessons": lessons,
        "materials": materials,
        "directory_evidence": _compact_directory_evidence(
            directory_evidence
        ),
    }


def _compact_directory_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {
        field: value[field]
        for field in (
            "strategy",
            "total_unit_count",
            "scanned_unit_indices",
            "toc_entries",
            "resolved_ranges",
            "anchors",
            "printed_to_pdf_offset",
            "confidence",
            "issues",
            "full_page_text_sent",
        )
        if field in value
    }


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _selected_fields(
    value: object,
    fields: tuple[str, ...],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {field: value[field] for field in fields if field in value}


class _DispatchAwareCompletions:
    def __init__(
        self,
        target: object,
        callback: Callable[[], None],
    ) -> None:
        self._target = target
        self._callback = callback
        self._dispatched = False

    def create(self, **kwargs: object) -> object:
        if self._dispatched:
            raise TeachingPrepValidationError(
                "semester mapping attempted more than one physical request"
            )
        create = getattr(self._target, "create", None)
        if not callable(create):
            raise TeachingPrepValidationError(
                "semester mapping model client is unavailable"
            )
        self._dispatched = True
        self._callback()
        return create(**kwargs)


def _client_with_dispatch_callback(
    client: object,
    callback: Callable[[], None] | None,
) -> object:
    if callback is None:
        return client
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=_DispatchAwareCompletions(completions, callback)
        )
    )


__all__ = ["WorkspaceSemesterMappingModelAdapter"]
