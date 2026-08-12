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
    ("class_teacher", "class_teacher.intake_triage"): TaskPresentation(
        "class_teacher",
        "class_teacher.intake_triage",
        "班主任 · 整理一项事务",
        "班主任",
    ),
    ("class_teacher", "class_teacher.draft_revision"): TaskPresentation(
        "class_teacher",
        "class_teacher.draft_revision",
        "班主任 · 调整一份事务草稿",
        "班主任",
    ),
    ("class_teacher", "class_teacher.affair_flow_revision"): TaskPresentation(
        "class_teacher",
        "class_teacher.affair_flow_revision",
        "班主任 · 调整事务流程",
        "班主任",
    ),
}

_RECOVERY_ONLY_TASKS = frozenset(
    {
        ("class_teacher", "class_teacher.intake"),
    }
)

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


_ERROR_DETAILS = {
    "semester_mapping_retry_available": (
        "本次整理没有产出结果；重新检查发送范围后可以再试一次。"
    ),
    "semester_mapping_response_failed_local_validation": (
        "模型建议未通过本机校验，本次整理没有产出结果。"
    ),
    "semester_mapping_unexplained_coverage_gap": (
        "资料中有页面既没有对应到课时，模型也没有说明原因，本次整理没有产出结果。"
    ),
    "semester_mapping_existing_tree_replaced": (
        "模型试图改动已有的正式课时树，本次整理没有产出结果。"
    ),
    "semester_mapping_unavailable_lesson": (
        "模型把页面关联到了不存在的课时，本次整理没有产出结果。"
    ),
    "semester_mapping_duplicate_lesson_decision": (
        "模型对同一课时给出了重复结论，本次整理没有产出结果。"
    ),
    "semester_mapping_model_semantic_contract_violation": (
        "模型返回的内容不符合整理要求，本次整理没有产出结果。"
    ),
    "semester_mapping_model_semantic_evidence_mismatch": (
        "模型引用的目录线索与本机解析结果对不上，本次整理没有产出结果。"
    ),
    "semester_mapping_model_semantic_evidence_incomplete": (
        "模型没有覆盖资料目录的全部条目，本次整理没有产出结果。"
    ),
    "semester_mapping_model_response_truncated": (
        "模型返回的内容不完整，本次整理没有产出结果。"
    ),
    "semester_mapping_model_response_invalid_json": (
        "模型返回的内容无法读取，本次整理没有产出结果。"
    ),
    "semester_mapping_model_response_invalid_type": (
        "模型返回的内容无法读取，本次整理没有产出结果。"
    ),
    "semester_mapping_model_response_text_unavailable": (
        "模型没有返回可用内容，本次整理没有产出结果。"
    ),
    "semester_mapping_model_response_invalid": (
        "模型建议未通过本机校验，本次整理没有产出结果。"
    ),
    "semester_mapping_model_configuration_invalid": (
        "模型配置不可用，本次没有调用模型。"
    ),
    "semester_mapping_model_parameter_incompatible": (
        "当前模型不接受本次请求的参数，本次整理没有产出结果。"
    ),
}


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
    expected_prefix = "teaching_prep." if module == "teaching_prep" else "class_teacher."
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
