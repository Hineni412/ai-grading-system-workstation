from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

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


_SYSTEM_INSTRUCTION = """\
你是初中数学学期资料目录整理助手。只依据给出的学期快照工作。
返回单个 json（JSON）对象，只能包含 tree、mappings、uncertainties。
如果快照已经有课时树，tree 必须为空，只把新增资料映射到 existing lesson id。
如果课时树为空，tree 按章、节、课时三级给出，每个节点使用简短唯一 key；
mapping 的 lesson_ref 对新课时使用 proposal:<lesson key>。
每条 mapping 只能包含 material_record_id、lesson_ref、start_unit、end_unit。
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
                            semester_snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
            },
            timeout_override_seconds=600,
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
