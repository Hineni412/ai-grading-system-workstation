from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.infrastructure.llm.lesson_model import (
    _response_text,
)
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)


_SYSTEM_INSTRUCTION = """\
你是初中数学学期资料目录整理助手。只依据给出的学期快照工作。
返回单个 JSON 对象，只能包含 tree、mappings、uncertainties。
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
    ) -> dict[str, Any]:
        response = self.gateway.chat_completions(
            request=WorkspaceModelRequest(
                purpose="semester_mapping",
                data_classification="teaching_material_aggregate",
                operation_id=operation_id,
            ),
            client=self.client,
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
            payload = json.loads(_response_text(response))
        except json.JSONDecodeError as exc:
            raise TeachingPrepValidationError(
                "semester mapping model returned invalid JSON"
            ) from exc
        if not isinstance(payload, Mapping):
            raise TeachingPrepValidationError(
                "semester mapping model response must be an object"
            )
        return dict(payload)


__all__ = ["WorkspaceSemesterMappingModelAdapter"]
