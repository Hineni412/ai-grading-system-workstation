from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)

from .lesson_model import _response_text, vision_user_content


_SYSTEM_INSTRUCTION = """\
你是初中数学备课资料定位助手。只能查看用户提供的冻结参考范围快照，
不得引用快照外资料。返回单个 json（JSON）对象且只能包含 suggestions。
只允许从 materials 中 purpose=exercise 或 purpose=textbook 的原页提出候选题；
不得从参考 PPT、答案资料中截题，也不得把整页当作一道题。优先识别题号、
题干、图形和同题小问的完整边界，crop 必须尽量紧贴题目且不能包含答案区。
每条 suggestion 只能包含 material_version_id、question_number、content_label、
difficulty、classroom_use、estimated_minutes、teaching_focus、reason、
uncertainties、question_regions、answer_regions。每个区域只能包含
material_unit_id、sequence、crop；crop 使用 0 到 1 的 x0、y0、x1、y1。
difficulty 与 classroom_use 必须返回下面的英文代码；uncertainties 即使为空也必须返回 []，
crop 必须返回 {"x0":数字,"y0":数字,"x1":数字,"y1":数字} 对象，不能返回数组。
没有答案区域时 answer_regions 必须返回 []，禁止用全零坐标作为占位框。
difficulty 只能使用英文代码 unrated、easy、medium、hard；classroom_use 只能使用
英文代码 introduction、example、guided_practice、independent_practice、diagnostic、
challenge、summary，禁止把这些代码翻译成中文。
只提出等待系统自动采用的候选，不得确认答案、写入题库、修改课件或推断学生个人情况。
"""


class WorkspaceExerciseSuggestionModelAdapter:
    """Real-call seam for one explicitly confirmed reference snapshot."""

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
                "exercise suggestion model is required"
            )

    def generate(
        self,
        *,
        operation_id: str,
        reference_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = dict(reference_snapshot)
        raw_images = snapshot.pop("reference_images", [])
        response = self.gateway.chat_completions(
            request=WorkspaceModelRequest(
                purpose="exercise_suggestions",
                data_classification="selected_teaching_material_ranges",
                operation_id=operation_id,
            ),
            client=self.client,
            model=self.model,
            kwargs={
                "messages": [
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {
                        "role": "user",
                        "content": vision_user_content(
                            snapshot, raw_images, max_images=16
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
            },
            timeout_override_seconds=110,
        )
        try:
            payload = json.loads(_response_text(response))
        except json.JSONDecodeError as exc:
            raise TeachingPrepValidationError(
                "exercise suggestion model returned invalid JSON"
            ) from exc
        if not isinstance(payload, Mapping):
            raise TeachingPrepValidationError(
                "exercise suggestion model response must be an object"
            )
        return dict(payload)


__all__ = ["WorkspaceExerciseSuggestionModelAdapter"]
