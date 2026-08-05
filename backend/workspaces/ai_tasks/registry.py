from __future__ import annotations

from dataclasses import dataclass


DESTINATION_KEYS = frozenset(
    {
        "teaching_prep.overview",
        "teaching_prep.library",
        "teaching_prep.lesson.materials",
        "teaching_prep.lesson.plan",
        "teaching_prep.lesson.exercises",
        "teaching_prep.lesson.slides",
        "teaching_prep.lesson.package",
        "class_teacher.home",
        "class_teacher.student.record",
        "class_teacher.affair.record",
        "class_teacher.plan.calendar",
        "class_teacher.affair.sop",
    }
)


@dataclass(frozen=True, slots=True)
class TaskPresentation:
    module: str
    task_kind: str
    safe_title: str
    safe_source: str


_PRESENTATIONS = {
    ("teaching_prep", "teaching_prep.semester_mapping"): TaskPresentation(
        "teaching_prep", "teaching_prep.semester_mapping", "备课 · 整理学期资料", "备课"
    ),
    ("teaching_prep", "teaching_prep.lesson_plan"): TaskPresentation(
        "teaching_prep", "teaching_prep.lesson_plan", "备课 · 形成课堂方案", "备课"
    ),
    ("teaching_prep", "teaching_prep.exercise_suggestions"): TaskPresentation(
        "teaching_prep", "teaching_prep.exercise_suggestions", "备课 · 整理候选练习", "备课"
    ),
    ("teaching_prep", "teaching_prep.slide_change_proposal"): TaskPresentation(
        "teaching_prep", "teaching_prep.slide_change_proposal", "备课 · 检查课件修改方案", "备课"
    ),
    ("class_teacher", "class_teacher.intake"): TaskPresentation(
        "class_teacher", "class_teacher.intake", "班主任 · 整理一项事务", "班主任"
    ),
}

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


def presentation(module: str, task_kind: str) -> TaskPresentation:
    try:
        return _PRESENTATIONS[(module, task_kind)]
    except KeyError as exc:
        raise ValueError("workspace AI task kind is not registered") from exc


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
    expected_prefix = "teaching_prep." if module == "teaching_prep" else "class_teacher."
    if not destination_key.startswith(expected_prefix):
        raise ValueError("workspace handoff cannot cross modules")


__all__ = [
    "DESTINATION_KEYS",
    "TaskPresentation",
    "assert_destination",
    "message_for",
    "presentation",
]
