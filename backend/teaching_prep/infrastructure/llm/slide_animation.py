from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)

from .lesson_model import _response_text


_SYSTEM_INSTRUCTION = """\
你是初中数学课堂演示分镜助手。只能依据用户提供的已选课件页预览图和抽出文字，
为教师生成可在浏览器离线翻页播放的课堂动画分镜。
返回单个 json（JSON）对象，只能包含 title 和 scenes。
title 是整段动画的课堂标题，1 到 80 个字符。
scenes 是数组，1 到 12 项。每项只能包含 title、narration、duration_ms、source_page，
可选 highlight。
source_page 必须是用户提供的页码之一。
narration 是教师可朗读或展示的讲解，1 到 400 个字符。
duration_ms 是该分镜停留毫秒，800 到 12000。
禁止输出 HTML、脚本、外链、CSS 或可执行代码。
不得编造未提供的页码，不得改课件文件，不得给出学生诊断。
"""


class WorkspaceSlideAnimationModelAdapter:
    """One confirmed request that turns selected PPT pages into a storyboard."""

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
            raise TeachingPrepValidationError("slide animation model is required")

    def generate(
        self,
        *,
        operation_id: str,
        page_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(page_payload)
        raw_images = payload.pop("page_images", [])
        response = self.gateway.chat_completions(
            request=WorkspaceModelRequest(
                purpose="slide_animation",
                data_classification="selected_ppt_pages",
                operation_id=operation_id,
            ),
            client=self.client,
            model=self.model,
            kwargs={
                "messages": [
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {
                        "role": "user",
                        "content": _user_content(payload, raw_images),
                    },
                ],
                "response_format": {"type": "json_object"},
            },
            timeout_override_seconds=110,
        )
        try:
            parsed = json.loads(_response_text(response))
        except json.JSONDecodeError as exc:
            raise TeachingPrepValidationError(
                "slide animation model returned invalid JSON"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise TeachingPrepValidationError(
                "slide animation model response must be an object"
            )
        return dict(parsed)


def _user_content(
    page_payload: Mapping[str, object],
    raw_images: object,
) -> object:
    snapshot_json = json.dumps(
        page_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if not isinstance(raw_images, list) or not raw_images:
        return snapshot_json
    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": snapshot_json,
        }
    ]
    for raw in raw_images[:4]:
        if not isinstance(raw, Mapping):
            continue
        data = raw.get("content")
        if not isinstance(data, bytes) or not data:
            continue
        mime_type = str(raw.get("mime_type") or "image/png")
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            continue
        encoded = base64.b64encode(data).decode("ascii")
        content.extend(
            [
                {
                    "type": "text",
                    "text": f"课件原页预览；page_index={raw.get('page_index')}",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                },
            ]
        )
    return content


__all__ = ["WorkspaceSlideAnimationModelAdapter"]
