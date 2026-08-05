from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..errors import VaultError


DOMAINS = {
    "student_growth",
    "student_support",
    "conflict_safety",
    "class_operations",
    "activities_culture",
    "school_coordination",
}
HANDLING_MODES = {"record", "plan_calendar", "sop"}
INTENTS = {"create", "append", "follow_up", "plan", "review"}
DESTINATIONS = {
    "class_teacher.student.record",
    "class_teacher.affair.record",
    "class_teacher.plan.calendar",
    "class_teacher.affair.sop",
}
SAFETY_LEVELS = {"normal", "teacher_review_required", "urgent_attention"}
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
_TOP_LEVEL_FIELDS = {"contract_version", "assistant_message", "clarification_questions", "work_items"}
_WORK_ITEM_FIELDS = {
    "work_item_id", "domain", "primary_mode", "secondary_modes", "intent",
    "reason_summary", "subject_refs", "time_facts", "safety_level",
    "missing_fields", "draft", "draft_ref", "handoff_key",
}


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_item_id: str
    domain: str
    primary_mode: str
    secondary_modes: tuple[str, ...]
    intent: str
    reason_summary: str
    subject_refs: tuple[dict[str, str], ...]
    time_facts: tuple[dict[str, Any], ...]
    safety_level: str
    missing_fields: tuple[str, ...]
    content: dict[str, Any]
    destination_key: str


@dataclass(frozen=True, slots=True)
class TriageResult:
    assistant_message: str
    clarification_questions: tuple[str, ...]
    work_items: tuple[WorkItem, ...]


def _text(value: object, label: str, *, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise VaultError("class_teacher_triage_invalid_result", f"AI 返回的{label}无效", status_code=422)
    return value.strip()


def _string_list(value: object, label: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise VaultError("class_teacher_triage_invalid_result", f"AI 返回的{label}无效", status_code=422)
    return tuple(_text(item, label, maximum=400) for item in value)


def _destination(mode: str, domain: str, subject_refs: tuple[dict[str, str], ...]) -> str:
    if mode == "plan_calendar":
        return "class_teacher.plan.calendar"
    if mode == "sop":
        return "class_teacher.affair.sop"
    if domain in {"student_growth", "student_support"}:
        return "class_teacher.student.record"
    return "class_teacher.affair.record"


def parse_triage(payload: Mapping[str, object]) -> TriageResult:
    if set(payload) - _TOP_LEVEL_FIELDS:
        raise VaultError("class_teacher_triage_invalid_result", "AI 返回了未知的分诊字段", status_code=422)
    if payload.get("contract_version") != "class_teacher_triage.v1":
        raise VaultError("class_teacher_triage_invalid_result", "AI 返回的分诊版本不受支持", status_code=422)
    assistant_message = _text(payload.get("assistant_message"), "说明", maximum=2000)
    questions = _string_list(payload.get("clarification_questions", []), "补问", maximum=3)
    raw_items = payload.get("work_items", [])
    if not isinstance(raw_items, list) or len(raw_items) > 8 or (not questions and not raw_items):
        raise VaultError("class_teacher_triage_invalid_result", "AI 返回的事务清单无效", status_code=422)

    items: list[WorkItem] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回的事务清单无效", status_code=422)
        if set(raw) - _WORK_ITEM_FIELDS:
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回了未知的事务字段", status_code=422)
        work_item_id = _text(raw.get("work_item_id"), "事务编号", maximum=128)
        if _OPAQUE_ID.fullmatch(work_item_id) is None or work_item_id in seen:
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回了重复事务", status_code=422)
        seen.add(work_item_id)
        domain = _text(raw.get("domain"), "事务领域", maximum=64)
        mode = _text(raw.get("primary_mode"), "处理方式", maximum=64)
        intent = _text(raw.get("intent"), "处理意图", maximum=64)
        if domain not in DOMAINS or mode not in HANDLING_MODES or intent not in INTENTS:
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回了未知事务领域或处理方式", status_code=422)
        secondary = _string_list(raw.get("secondary_modes", []), "后续处理方式", maximum=2)
        if len(set(secondary)) != len(secondary) or any(item not in HANDLING_MODES or item == mode for item in secondary):
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回的后续处理方式无效", status_code=422)
        raw_refs = raw.get("subject_refs", [])
        if not isinstance(raw_refs, list) or len(raw_refs) > 50:
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回的学生引用无效", status_code=422)
        refs: list[dict[str, str]] = []
        for ref in raw_refs:
            if not isinstance(ref, Mapping) or set(ref) != {"kind", "id", "revision"} or ref.get("kind") != "student":
                raise VaultError("class_teacher_triage_invalid_result", "AI 返回的学生引用无效", status_code=422)
            subject_id = _text(ref.get("id"), "学生引用", maximum=128)
            if _OPAQUE_ID.fullmatch(subject_id) is None:
                raise VaultError("class_teacher_triage_invalid_result", "AI 返回的学生引用无效", status_code=422)
            refs.append({
                "kind": "student",
                "id": subject_id,
                "revision": _text(ref.get("revision"), "学生版本", maximum=128),
            })
        subject_refs = tuple(refs)
        requested_destination = raw.get("handoff_key")
        destination = _destination(mode, domain, subject_refs)
        if requested_destination is not None and requested_destination != destination:
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回的页面目标未获允许", status_code=422)
        content = raw.get("draft") or {}
        if not isinstance(content, Mapping):
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回的草稿无效", status_code=422)
        time_facts = raw.get("time_facts", [])
        if not isinstance(time_facts, list) or len(time_facts) > 30 or any(not isinstance(item, Mapping) for item in time_facts):
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回的时间信息无效", status_code=422)
        safety_level = _text(raw.get("safety_level", "normal"), "安全级别", maximum=64)
        if safety_level not in SAFETY_LEVELS:
            raise VaultError("class_teacher_triage_invalid_result", "AI 返回的安全级别无效", status_code=422)
        items.append(WorkItem(
            work_item_id=work_item_id,
            domain=domain,
            primary_mode=mode,
            secondary_modes=secondary,
            intent=intent,
            reason_summary=_text(raw.get("reason_summary"), "分诊理由", maximum=800),
            subject_refs=subject_refs,
            time_facts=tuple(dict(item) for item in time_facts),
            safety_level=safety_level,
            missing_fields=_string_list(raw.get("missing_fields", []), "缺失项", maximum=20),
            content=dict(content),
            destination_key=destination,
        ))
    return TriageResult(assistant_message, questions, tuple(items))


__all__ = [
    "DESTINATIONS",
    "DOMAINS",
    "HANDLING_MODES",
    "INTENTS",
    "TriageResult",
    "WorkItem",
    "parse_triage",
]
