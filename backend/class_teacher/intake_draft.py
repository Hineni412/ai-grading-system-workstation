from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .sop_baseline_service import SopBaselineService


def classify_template(source_text: str, recommended_route: str) -> str:
    """Choose a conservative local SOP family from the first teacher sentence."""
    text = str(source_text or "")
    if recommended_route == "affair":
        if any(word in text for word in ("受伤", "流血", "骨折", "昏迷", "急救")):
            return "baseline.student_injury"
        if any(word in text for word in ("欺凌", "霸凌", "长期排挤", "反复威胁")):
            return "baseline.suspected_bullying"
        return "baseline.student_conflict"
    if any(word in text for word in ("家长", "家校", "家访", "监护人")):
        return "baseline.family_communication"
    return "baseline.care_conversation"


def compose_sensitive_draft(
    *,
    source_text: str,
    recommended_route: str,
    resolved_date: str | None,
    model_payload: dict[str, Any] | None,
    model_questions: list[str] | None = None,
    student_aliases: list[str] | None = None,
) -> dict[str, Any]:
    """Build a complete, useful first draft even when the model only asks questions.

    The baseline owns safety and dependency semantics. Model output may enrich
    wording and unknowns, but cannot remove required work from the first draft.
    """
    payload = dict(model_payload or {})
    # The local classifier owns the safety baseline. Model output may tailor
    # wording and dates, but it cannot switch an incident to a weaker workflow.
    template_key = classify_template(source_text, recommended_route)
    baseline = SopBaselineService.preview(template_key)

    anchor = _date(resolved_date) or date.today()
    baseline_steps = list(baseline.get("steps") or [])
    model_steps = {
        str(item.get("key") or ""): item
        for item in list(payload.get("steps") or [])
        if isinstance(item, dict) and item.get("key")
    }
    model_calendar = {
        str(item.get("key") or ""): item
        for item in list(payload.get("calendar_items") or [])
        if isinstance(item, dict) and item.get("key")
    }
    levels = _dependency_levels(baseline_steps)
    steps: list[dict[str, Any]] = []
    calendar_items: list[dict[str, Any]] = []
    for raw in baseline_steps:
        key = str(raw["key"])
        suggestion = model_steps.get(key, {})
        due = anchor + timedelta(days=levels.get(key, 0))
        title = _limited_text(suggestion.get("title"), 160) or str(raw["title"])
        details = _limited_text(suggestion.get("details"), 500) or str(raw.get("details") or "")
        step = {
            "key": key,
            "title": title,
            "details": details,
            "depends_on": [str(item) for item in list(raw.get("depends_on") or [])],
            "required": bool(raw.get("required", True)),
            "waivable": bool(raw.get("waivable", False)),
            "safety_required": bool(raw.get("safety_required", False)),
            "activation": raw.get("activation"),
            "decision_key": raw.get("decision_key"),
            "decision_prompt": raw.get("decision_prompt"),
            "decision_options": list(raw.get("decision_options") or []),
        }
        steps.append(step)
        calendar_suggestion = model_calendar.get(f"calendar.{key}", {})
        suggested_due = _date(
            str(calendar_suggestion.get("due_date") or "") or None
        )
        calendar_items.append(
            {
                "key": f"calendar.{key}",
                "step_key": key,
                "title": _limited_text(calendar_suggestion.get("title"), 160) or title,
                "due_date": (suggested_due or due).isoformat(),
                "depends_on": [f"calendar.{item}" for item in step["depends_on"]],
            }
        )

    assumptions = _string_list(payload.get("assumptions"), maximum=8)
    questions = _string_list(model_questions, maximum=8)
    to_verify = _string_list(payload.get("to_verify"), maximum=8)
    to_verify.extend(item for item in questions if item not in to_verify)
    if not resolved_date:
        to_verify.append("各步骤日期按今天起的工作顺序初排，采用前请核对实际发生时间和校内时限。")
    to_verify = list(dict.fromkeys(to_verify))[:8]
    kind = (
        "affair_recommendation"
        if recommended_route == "affair"
        else "student_support_recommendation"
    )
    return {
        "kind": kind,
        "transaction_type": (
            "学生事务" if recommended_route == "affair" else "学生支持"
        ),
        "template_key": template_key,
        "title": _limited_text(payload.get("title"), 160) or str(baseline["title"]),
        "summary": _limited_text(payload.get("summary"), 800)
        or "已按首句信息形成可执行初稿；未确认的信息不会阻止教师先查看和调整方案。",
        "reasons": _string_list(payload.get("reasons"), maximum=8),
        "assumptions": assumptions,
        "to_verify": to_verify,
        "steps": steps,
        "edges": [
            {"source_key": parent, "target_key": str(step["key"]), "relation": "depends_on"}
            for step in steps
            for parent in list(step["depends_on"])
        ],
        "calendar_items": calendar_items,
        "student_aliases": list(dict.fromkeys(student_aliases or [])),
        "risk_level": str(baseline.get("risk_level") or "ordinary"),
        "emergency_prompt": baseline.get("emergency_prompt"),
        "school_config_gaps": list(baseline.get("school_config_gaps") or []),
        "workflow_scope": str(baseline.get("workflow_scope") or "personal_checklist"),
    }


def selected_with_downstream(
    draft: dict[str, Any], selected_steps: list[str], selected_calendar: list[str]
) -> tuple[list[str], list[str]]:
    """Normalize selection so a step always includes every downstream item."""
    steps = [item for item in list(draft.get("steps") or []) if isinstance(item, dict)]
    downstream: dict[str, set[str]] = {str(item.get("key") or ""): set() for item in steps}
    for item in steps:
        child = str(item.get("key") or "")
        for parent in list(item.get("depends_on") or []):
            downstream.setdefault(str(parent), set()).add(child)
    chosen = {item for item in selected_steps if item in downstream}
    frontier = list(chosen)
    while frontier:
        parent = frontier.pop()
        for child in downstream.get(parent, set()):
            if child not in chosen:
                chosen.add(child)
                frontier.append(child)
    calendar_items = [
        item
        for item in list(draft.get("calendar_items") or [])
        if isinstance(item, dict)
    ]
    calendar_keys = {str(item.get("key") or "") for item in calendar_items}
    calendar_downstream: dict[str, set[str]] = {
        key: set() for key in calendar_keys
    }
    for item in calendar_items:
        child = str(item.get("key") or "")
        for parent in list(item.get("depends_on") or []):
            calendar_downstream.setdefault(str(parent), set()).add(child)
    calendar_chosen = {item for item in selected_calendar if item in calendar_keys}
    calendar_chosen.update(f"calendar.{item}" for item in chosen)
    calendar_frontier = list(calendar_chosen)
    while calendar_frontier:
        parent = calendar_frontier.pop()
        for child in calendar_downstream.get(parent, set()):
            if child not in calendar_chosen:
                calendar_chosen.add(child)
                calendar_frontier.append(child)
    return sorted(chosen), sorted(calendar_chosen)


def merge_revision(
    base: dict[str, Any],
    candidate: dict[str, Any],
    selected_steps: list[str],
    selected_calendar: list[str],
) -> dict[str, Any]:
    """Apply AI changes only inside the teacher-selected scope."""
    step_scope, calendar_scope = selected_with_downstream(
        base, selected_steps, selected_calendar
    )
    candidate_steps = {
        str(item.get("key") or ""): item
        for item in list(candidate.get("steps") or [])
        if isinstance(item, dict)
    }
    merged_steps: list[dict[str, Any]] = []
    for old in list(base.get("steps") or []):
        key = str(old.get("key") or "")
        replacement = candidate_steps.get(key)
        if key not in step_scope or replacement is None:
            merged_steps.append(dict(old))
            continue
        updated = dict(old)
        for field in ("title", "details", "activation", "decision_prompt", "decision_options"):
            if field in replacement:
                updated[field] = replacement[field]
        # Safety, identity and dependency semantics belong to the baseline.
        merged_steps.append(updated)

    candidate_calendar = {
        str(item.get("key") or ""): item
        for item in list(candidate.get("calendar_items") or [])
        if isinstance(item, dict)
    }
    merged_calendar: list[dict[str, Any]] = []
    for old in list(base.get("calendar_items") or []):
        key = str(old.get("key") or "")
        replacement = candidate_calendar.get(key)
        if key not in calendar_scope or replacement is None:
            merged_calendar.append(dict(old))
            continue
        updated = dict(old)
        for field in ("title", "due_date"):
            if field in replacement:
                updated[field] = replacement[field]
        merged_calendar.append(updated)
    return {
        **base,
        "summary": (
            candidate.get("summary") or base.get("summary")
            if step_scope
            else base.get("summary")
        ),
        "assumptions": (
            _string_list(candidate.get("assumptions"), maximum=8)
            or list(base.get("assumptions") or [])
            if step_scope
            else list(base.get("assumptions") or [])
        ),
        "to_verify": (
            _string_list(candidate.get("to_verify"), maximum=8)
            or list(base.get("to_verify") or [])
            if step_scope
            else list(base.get("to_verify") or [])
        ),
        "steps": merged_steps,
        "calendar_items": merged_calendar,
        "selected_step_keys": step_scope,
        "selected_calendar_keys": calendar_scope,
    }


def _dependency_levels(steps: list[dict[str, Any]]) -> dict[str, int]:
    levels: dict[str, int] = {}
    remaining = {str(item["key"]): item for item in steps}
    while remaining:
        progressed = False
        for key, item in list(remaining.items()):
            parents = [str(value) for value in list(item.get("depends_on") or [])]
            if all(parent in levels for parent in parents):
                levels[key] = max((levels[parent] + 1 for parent in parents), default=0)
                del remaining[key]
                progressed = True
        if not progressed:
            for key in remaining:
                levels[key] = 0
            break
    return levels


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _limited_text(value: object, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = " ".join(value.split())
    return clean[:maximum] if clean else None


def _string_list(value: object, *, maximum: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:maximum]:
        text = _limited_text(item, 240)
        if text:
            result.append(text)
    return list(dict.fromkeys(result))


__all__ = [
    "classify_template",
    "compose_sensitive_draft",
    "merge_revision",
    "selected_with_downstream",
]
