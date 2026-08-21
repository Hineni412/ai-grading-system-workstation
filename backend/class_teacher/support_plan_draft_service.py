from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from backend.llm.json_repair import parse_json_object_locally
from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway

from .errors import VaultError
from .model_approval import ModelDestinationChanged, ModelDispatchDisabled


_PURPOSE = "class_teacher_support_plan_draft"
_GOAL_MAX_CHARS = 1000
_ACTION_MAX_CHARS = 200
_ACTION_MAX_COUNT = 30
_REVIEW_FALLBACK_DAYS = 14

_SYSTEM_PROMPT = (
    "你是班主任的学生支持方案起草助手。根据教师提供的学生当前档案与支持情况，"
    "起草一份支持方案草稿，由教师核对修改后决定是否保存。"
    "只输出一个 JSON 对象，不要输出任何其他文字或解释，格式为："
    '{"contract_version":"class_teacher_support_plan_draft.v1",'
    '"goal":"方案目标（一句话，1000 字以内）",'
    '"support_actions":["一条具体行动"],'
    '"review_at":"YYYY-MM-DD"}。'
    "要求：方案要与档案中的支持重点和已验证有效的做法相连；"
    "每条行动具体、一两周内可以执行，1 到 30 条，每条 200 字以内；"
    "复查日期建议在今天之后 7 到 21 天。"
    "只起草支持方案，不作诊断或评判；不编造档案中没有的事实。"
)


class SupportPlanDraftService:
    """Draft one support plan from the current student profile, never persisted.

    The configured model is called at most once per operation through the
    shared zero-retry Task gateway; the teacher reviews, edits, and saves the
    draft through the ordinary plan-creation flow.
    """

    def __init__(
        self,
        *,
        model_gateway,
        student_cards,
        support,
    ) -> None:
        self.model_gateway = model_gateway
        self.student_cards = student_cards
        self.support = support

    def draft_plan(
        self,
        *,
        token: str,
        subject_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        self.support.get_subject(token=token, subject_id=subject_id)
        if self.model_gateway is None:
            raise VaultError(
                "support_plan_draft_unavailable",
                "当前班主任模型不可用，无法起草方案；可以手动填写",
                status_code=422,
            )
        context = self.student_cards.model_context(token=token, subject_id=subject_id)
        fingerprint = str(
            self.model_gateway.destination_snapshot().get("destination_fingerprint")
            or ""
        )
        messages = (
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "当前学生档案与支持情况："
                + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            },
        )
        try:
            raw = self.model_gateway.invoke_workspace_task(
                task_gateway=WorkspaceAITaskModelGateway(),
                messages=messages,
                operation_id=operation_id,
                purpose=_PURPOSE,
                expected_destination_fingerprint=fingerprint,
            )
        except ModelDestinationChanged as exc:
            raise VaultError(
                "support_plan_draft_model_destination_changed",
                "模型配置已经变化；起草请求尚未发送",
                status_code=409,
            ) from exc
        except ModelDispatchDisabled as exc:
            raise VaultError(
                "support_plan_draft_unavailable",
                "当前班主任模型不可用，无法起草方案；可以手动填写",
                status_code=422,
            ) from exc
        except Exception as exc:
            if self.model_gateway.physical_request_count(operation_id):
                raise VaultError(
                    "support_plan_draft_result_unknown",
                    "起草请求可能已经发出，但没有可靠结果；系统不会自动重发",
                    status_code=502,
                ) from exc
            raise VaultError(
                "support_plan_draft_failed_before_dispatch",
                "起草请求尚未发出，可以重试或手动填写",
                status_code=503,
            ) from exc
        return _soften(raw)


def _soften(raw: str) -> dict[str, object]:
    try:
        payload = parse_json_object_locally(raw).payload
    except (TypeError, ValueError) as exc:
        raise _invalid_result() from exc
    goal = str(payload.get("goal") or "").strip()[:_GOAL_MAX_CHARS]
    raw_actions = payload.get("support_actions")
    actions = [
        str(item).strip()[:_ACTION_MAX_CHARS]
        for item in (raw_actions if isinstance(raw_actions, list) else [])
    ]
    actions = [item for item in actions if item][:_ACTION_MAX_COUNT]
    if not goal or not actions:
        raise _invalid_result()
    return {
        "goal": goal,
        "support_actions": actions,
        "review_at": _review_at(payload.get("review_at")),
    }


def _review_at(value: object) -> str:
    text = str(value or "").strip()
    if text:
        try:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    return (datetime.now(UTC).date() + timedelta(days=_REVIEW_FALLBACK_DAYS)).isoformat()


def _invalid_result() -> VaultError:
    return VaultError(
        "support_plan_draft_invalid_result",
        "模型没有返回可用的方案草稿；可以重试或手动填写",
        status_code=422,
    )


__all__ = ["SupportPlanDraftService"]
