from __future__ import annotations

from dataclasses import dataclass


DESTINATION_KEYS: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class TaskPresentation:
    module: str
    task_kind: str
    safe_title: str
    safe_source: str


_PRESENTATIONS: dict[tuple[str, str], TaskPresentation] = {}

_RECOVERY_ONLY_TASKS: frozenset[tuple[str, str]] = frozenset()

_MESSAGES = {
    "prepared": ("任务已经准备好，尚未发送。", "返回来源页确认发送"),
    "queued": ("任务已排队，尚未发送。", "等待或取消"),
    "running": ("任务正在处理。", "等待任务更新"),
    "needs_input": ("还需要教师补充信息。", "返回来源页补充信息"),
    "proposal_ready": ("AI 建议已经保存，尚未写入正式业务数据。", "审阅草稿"),
    "failed_before_dispatch": ("任务在发送前停止，没有发生模型发送。", "检查后继续首次发送"),
    "failed": ("模型已返回或调用已结束，但任务没有完成。", "查看说明后新建一次操作"),
    "result_unknown": ("请求可能已经发出，但本机没有可确认的结果。", "查询原任务或放弃"),
    "invalid_result": ("模型结果未通过本地校验。", "查看说明后新建一次操作"),
    "cancelled_before_dispatch": ("任务已在发送前取消。", "返回来源页"),
    "discarded": ("任务结果已放弃，没有写入正式业务数据。", "返回来源页"),
}


_ERROR_DETAILS = {}


def error_detail_for(error_code: str | None) -> str | None:
    """Return a teacher-readable reason for a known task failure code."""

    if not error_code:
        return None
    return _ERROR_DETAILS.get(str(error_code))


def presentation(module: str, task_kind: str) -> TaskPresentation:
    try:
        return _PRESENTATIONS[(module, task_kind)]
    except KeyError as exc:
        raise ValueError("workspace AI task kind is not registered") from exc


def is_recovery_only_task(module: str, task_kind: str) -> bool:
    """Return whether a registered legacy kind may only finish persisted work."""

    return (module, task_kind) in _RECOVERY_ONLY_TASKS


def message_for(status: str, evidence: str) -> tuple[str, str]:
    message, action = _MESSAGES.get(
        status,
        ("任务状态暂时无法解释。", "返回来源页"),
    )
    if status == "running" and evidence == "not_started":
        return "任务正在准备，尚未发送。", "等待或取消"
    if status == "running" and evidence == "may_have_started":
        return "请求可能已经发出，正在等待结果。", "等待或停止后续处理"
    return message, action


def assert_destination(module: str, destination_key: str) -> None:
    if destination_key not in DESTINATION_KEYS:
        raise ValueError("workspace handoff destination is not allowed")
    expected_prefix = f"{module}."
    if not destination_key.startswith(expected_prefix):
        raise ValueError("workspace handoff cannot cross modules")


__all__ = [
    "DESTINATION_KEYS",
    "TaskPresentation",
    "assert_destination",
    "error_detail_for",
    "is_recovery_only_task",
    "message_for",
    "presentation",
]
