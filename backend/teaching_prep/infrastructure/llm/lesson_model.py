from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)


_SYSTEM_INSTRUCTION = """\
你是初中数学备课草稿助手。只能依据用户提供的已冻结资源包。
返回单个 JSON 对象，只能包含 knowledge_objectives、focus_points、
anticipated_difficulties、lesson_flow、exercise_recommendations、
slide_adaptations、uncertainties。必须遵守资源包中的
preparation_preferences：它是教师本次明确选择的倾向。slide_adaptations
逐页给出 slide_ref、role、action、textbook_refs、reason、citations；
只允许建议 keep 或 delete，不得输出 WPS 指令。教材页码只能通过
textbook_refs 引用资源包内真实教材页，不能写猜测页码。练习删减应优先处理
课件后段过量练习，保留讲授例题和短题；补题不得超过偏好上限，且应避免与
原课件重复或直接照搬作业教辅原题。所有结论必须引用资源包内已有 citation
ID；不得编造页码、题号、候选题或班级结论。课堂总时长由本机另行计算。
"""


class WorkspaceLessonModelAdapter:
    """Real-call seam; construction and use require explicit caller authority."""

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
            raise TeachingPrepValidationError("lesson model is required")

    def generate(
        self,
        *,
        operation_id: str,
        resource_pack: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.gateway.chat_completions(
            request=WorkspaceModelRequest(
                purpose="lesson_draft",
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
                            resource_pack,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
            },
        )
        text = _response_text(response)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TeachingPrepValidationError(
                "lesson model returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise TeachingPrepValidationError(
                "lesson model response must be an object"
            )
        return payload


def _response_text(response: object) -> str:
    if isinstance(response, Mapping):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, Mapping):
                message = choice.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    raise TeachingPrepValidationError(
        "lesson model response text is unavailable"
    )


__all__ = ["WorkspaceLessonModelAdapter"]
