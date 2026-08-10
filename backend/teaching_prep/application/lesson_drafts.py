from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.domain.models import ResourcePackVersion
from backend.teaching_prep.application.preferences import (
    resolve_teaching_preferences,
)


_PHASE_MINUTES = {
    "introduction": 3,
    "exploration": 8,
    "example": 8,
    "practice": 6,
    "summary": 3,
}
_PHASE_LABELS = {
    "introduction": "导入",
    "exploration": "探索",
    "example": "例题",
    "practice": "练习",
    "summary": "总结",
}
_RECOMMENDATION_ACTIONS = {
    "include",
    "backup",
    "move_after_class",
    "exclude",
    "replace_shorter",
}
_REQUIRED_TOP_LEVEL_KEYS = {
    "knowledge_objectives",
    "focus_points",
    "anticipated_difficulties",
    "lesson_flow",
    "exercise_recommendations",
    "uncertainties",
}
_OPTIONAL_TOP_LEVEL_KEYS = {"slide_adaptations"}
_SLIDE_ROLES = {
    "introduction",
    "exploration",
    "explanation",
    "example",
    "practice",
    "summary",
    "other",
}
_EXPLICIT_LOCATOR = re.compile(
    r"第\s*(?P<number>\d{1,6})\s*(?P<kind>页|张|题)"
)


def draft_preflight(
    pack: ResourcePackVersion,
    *,
    mode: str,
    model_available: bool,
    model_label: str | None,
) -> dict[str, object]:
    references = reference_catalog(pack)
    payload = pack.payload
    evidence = _mapping(payload.get("evidence"))
    assessment = _mapping(evidence.get("assessment"))
    return {
        "resource_pack_id": pack.id,
        "resource_pack_version": pack.version_number,
        "resource_pack_sha256": pack.pack_sha256,
        "mode": mode,
        "will_call_model": mode == "model",
        "model_available": model_available,
        "model_label": model_label if mode == "model" else "本地模板",
        "data_scope": {
            "material_units": sum(
                len(_list(_mapping(item).get("units")))
                for item in _list(payload.get("materials"))
            ),
            "exercise_candidates": len(_list(payload.get("exercises"))),
            "question_evidence_items": len(
                _list(_mapping(evidence.get("question")).get("items"))
            ),
            "assessment_count": len(_list(assessment.get("assessments"))),
            "prior_review_count": len(_list(payload.get("prior_reviews"))),
            "anonymous_class_aggregate_only": True,
        },
        "references": [
            {"id": source_id, "label": label}
            for source_id, label in references.items()
        ],
        "missing_and_uncertain_count": len(
            _list(payload.get("missing_and_uncertain"))
        ),
        "preparation_preferences": resolve_teaching_preferences(
            payload.get("preparation_preferences")
        ),
    }


def build_local_template(
    pack: ResourcePackVersion,
) -> dict[str, object]:
    payload = pack.payload
    lesson = _mapping(payload.get("lesson"))
    lesson_id = str(lesson.get("lesson_node_id") or pack.lesson_node_id)
    lesson_title = str(lesson.get("title") or "本课")
    lesson_ref = f"lesson:{lesson_id}"
    refs = reference_catalog(pack)
    material_refs = [
        source_id
        for source_id in refs
        if source_id.startswith("material:")
    ]
    textbook_refs = _material_references(payload, "textbook")
    ppt_refs = _material_references(payload, "reference_ppt")
    exercise_sources = _exercise_sources(payload)
    question_sources = _question_sources(payload)
    evidence = _mapping(payload.get("evidence"))
    assessment = _mapping(evidence.get("assessment"))
    assessment_refs = [
        f"assessment:{item.get('assessment_id')}"
        for item in _list(assessment.get("assessments"))
        if isinstance(item, Mapping) and item.get("assessment_id") is not None
    ]
    base_material_ref = (
        textbook_refs[0]
        if textbook_refs
        else material_refs[0]
        if material_refs
        else lesson_ref
    )
    objectives = [
        {
            "text": f"围绕“{lesson_title}”完成本课知识理解与基本应用。",
            "citations": [lesson_ref, base_material_ref],
        }
    ]
    focus_points = [
        {
            "kind": "key",
            "title": lesson_title,
            "rationale": "以教师确认的教材范围和本课课时目标为主线。",
            "citations": [base_material_ref, lesson_ref],
        }
    ]
    knowledge_summary = _list(assessment.get("knowledge_summary"))
    anticipated: list[dict[str, object]] = []
    if knowledge_summary and assessment_refs:
        item = _mapping(knowledge_summary[0])
        knowledge = str(item.get("knowledge_point") or lesson_title)
        rate = item.get("observed_error_rate")
        rate_text = (
            f"观察错误率约 {round(float(rate) * 100)}%"
            if isinstance(rate, (int, float))
            else "现有班级汇总显示需要关注"
        )
        anticipated.append(
            {
                "text": f"{knowledge}：{rate_text}，建议在例题后安排即时检查。",
                "citations": [assessment_refs[0]],
            }
        )
        focus_points.append(
            {
                "kind": "difficulty",
                "title": knowledge,
                "rationale": "来自教师所选考试的匿名班级汇总证据。",
                "citations": [assessment_refs[0]],
            }
        )
    else:
        anticipated.append(
            {
                "text": "当前没有可用的本班学情依据，困难点需由教师课堂观察确认。",
                "citations": [lesson_ref],
            }
        )
    for raw_review in _list(payload.get("prior_reviews"))[:5]:
        review = _mapping(raw_review)
        review_id = str(review.get("review_id") or "")
        review_payload = _mapping(review.get("payload"))
        reteach_points = [
            str(item)
            for item in _list(review_payload.get("reteach_points"))
            if str(item).strip()
        ]
        if review_id and reteach_points:
            anticipated.append(
                {
                    "text": (
                        "上次本班课后复盘建议关注："
                        + "；".join(reteach_points[:3])
                    ),
                    "citations": [f"post_review:{review_id}"],
                }
            )
    slide_adaptations = _local_slide_adaptations(payload)
    insertion_slide_ref = _preferred_insertion_slide(slide_adaptations)
    exercise_recommendations: list[dict[str, object]] = []
    included = 0
    preferences = resolve_teaching_preferences(
        payload.get("preparation_preferences")
    )
    supplement_enabled = bool(
        preferences.get("supplement_from_references", True)
    )
    raw_limit = preferences.get("supplement_question_limit", 2)
    supplement_limit = (
        int(raw_limit)
        if isinstance(raw_limit, int) and not isinstance(raw_limit, bool)
        else 2
    )
    ranked_sources = _rank_supplement_sources(
        payload,
        exercise_sources,
        question_sources,
        preferences,
    )
    for source_ref, item, eligible, preference_reason in ranked_sources:
        selection = str(item.get("selection_status") or "")
        action = (
            "include"
            if (
                supplement_enabled
                and selection != "backup"
                and eligible
                and included < supplement_limit
            )
            else "backup"
        )
        if action == "include":
            included += 1
        title = str(
            item.get("content_label")
            or item.get("question_text")
            or item.get("text_excerpt")
            or item.get("question_number")
            or "候选题"
        )[:160]
        exercise_recommendations.append(
            {
                "source_ref": source_ref,
                "action": action,
                "target_slide_ref": (
                    insertion_slide_ref if action == "include" else None
                ),
                "title": title,
                "reason": (
                    "作为本课关键检查题。"
                    if action == "include"
                    else preference_reason
                    or "保留为课堂时间允许时的备用题。"
                ),
                "estimated_minutes": _positive_minutes(
                    item.get("estimated_minutes"),
                    default=4,
                ),
                "citations": [source_ref],
            }
        )
    flow_refs = {
        "introduction": [lesson_ref],
        "exploration": [base_material_ref],
        "example": [ppt_refs[0] if ppt_refs else base_material_ref],
        "practice": [
            exercise_recommendations[0]["source_ref"]
            if exercise_recommendations
            else base_material_ref
        ],
        "summary": [lesson_ref],
    }
    lesson_flow = [
        {
            "phase": phase,
            "title": _PHASE_LABELS[phase],
            "purpose": {
                "introduction": "明确本课问题与学习方向",
                "exploration": "依托教材活动形成新知",
                "example": "沿用已确认的例题与讲解顺序",
                "practice": "用少量关键题检查理解",
                "summary": "回扣目标并暴露仍需确认的问题",
            }[phase],
            "suggested_minutes": minimum,
            "citations": flow_refs[phase],
        }
        for phase, minimum in _PHASE_MINUTES.items()
    ]
    uncertainties = _uncertainties(payload)
    if not assessment_refs:
        uncertainties.insert(0, "当前无可用学情依据，不据此推断本班困难。")
    return {
        "knowledge_objectives": objectives,
        "focus_points": focus_points,
        "anticipated_difficulties": anticipated,
        "lesson_flow": lesson_flow,
        "exercise_recommendations": exercise_recommendations,
        "slide_adaptations": slide_adaptations,
        "uncertainties": uncertainties,
    }


def validate_draft_payload(
    value: object,
    pack: ResourcePackVersion,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TeachingPrepValidationError("lesson draft must be an object")
    keys = set(value)
    if (
        not _REQUIRED_TOP_LEVEL_KEYS.issubset(keys)
        or keys - _REQUIRED_TOP_LEVEL_KEYS - _OPTIONAL_TOP_LEVEL_KEYS
    ):
        raise TeachingPrepValidationError(
            "lesson draft has missing or unsupported sections"
        )
    allowed_refs = set(reference_catalog(pack))
    allowed_exercises = {
        source_id
        for source_id in allowed_refs
        if source_id.startswith(("exercise:", "question:"))
    }
    result: dict[str, object] = {
        "knowledge_objectives": _claims(
            value["knowledge_objectives"],
            allowed_refs,
            maximum=20,
        ),
        "focus_points": _focus_points(
            value["focus_points"],
            allowed_refs,
        ),
        "anticipated_difficulties": _claims(
            value["anticipated_difficulties"],
            allowed_refs,
            maximum=20,
        ),
        "lesson_flow": _flow(value["lesson_flow"], allowed_refs),
        "exercise_recommendations": _recommendations(
            value["exercise_recommendations"],
            allowed_refs,
            allowed_exercises,
            set(_material_references(pack.payload, "reference_ppt")),
        ),
        "slide_adaptations": _slide_adaptations(
            value.get("slide_adaptations", []),
            pack,
            allowed_refs,
        ),
        "uncertainties": _strings(
            value["uncertainties"],
            maximum=100,
            item_maximum=500,
        ),
    }
    preferences = resolve_teaching_preferences(
        pack.payload.get("preparation_preferences")
    )
    supplement_enabled = bool(
        preferences.get("supplement_from_references", True)
    )
    raw_limit = preferences.get("supplement_question_limit", 2)
    supplement_limit = (
        int(raw_limit)
        if isinstance(raw_limit, int) and not isinstance(raw_limit, bool)
        else 2
    )
    included_count = sum(
        1
        for raw in _list(result.get("exercise_recommendations"))
        if _mapping(raw).get("action") == "include"
    )
    if included_count > supplement_limit or (
        not supplement_enabled and included_count
    ):
        raise TeachingPrepValidationError(
            "draft exceeds the frozen supplement preference"
        )
    _validate_explicit_locators(result, pack)
    return result


def normalize_model_draft_payload(
    value: object,
    pack: ResourcePackVersion,
) -> object:
    """Apply only lossless or conservative repairs before strict validation."""
    if not isinstance(value, Mapping):
        return value
    normalized = dict(value)
    raw_focus_points = value.get("focus_points")
    if isinstance(raw_focus_points, list):
        normalized["focus_points"] = [
            (
                {**item, "kind": "key"}
                if isinstance(item, Mapping) and not str(item.get("kind") or "").strip()
                else item
            )
            for item in raw_focus_points
        ]
    allowed_exercises = {
        source_id
        for source_id in reference_catalog(pack)
        if source_id.startswith(("exercise:", "question:"))
    }
    if allowed_exercises:
        return normalized
    raw_recommendations = value.get("exercise_recommendations")
    if not isinstance(raw_recommendations, list) or not raw_recommendations:
        return normalized
    normalized["exercise_recommendations"] = []
    uncertainties = (
        list(value.get("uncertainties"))
        if isinstance(value.get("uncertainties"), list)
        else []
    )
    notice = (
        "教辅页尚未切分为可精确引用的候选题，本次未纳入模型提出的补题建议。"
    )
    if notice not in uncertainties:
        uncertainties.append(notice)
    normalized["uncertainties"] = uncertainties
    return normalized


def _local_slide_adaptations(
    payload: Mapping[str, object],
) -> list[dict[str, object]]:
    preferences = resolve_teaching_preferences(
        payload.get("preparation_preferences")
    )
    trim_enabled = bool(preferences.get("trim_excess_practice", True))
    preserve_examples = bool(preferences.get("preserve_teaching_examples", True))
    prefer_short = bool(preferences.get("prefer_short_practice", True))
    trim_level = str(preferences.get("practice_trim_level") or "moderate")
    threshold = {"light": 220, "moderate": 140, "strong": 80}.get(
        trim_level, 140
    )
    tail_ratio = {"light": 0.85, "moderate": 0.70, "strong": 0.60}.get(
        trim_level, 0.70
    )
    textbook_units = _material_units(payload, "textbook")
    result: list[dict[str, object]] = []
    for raw_material in _list(payload.get("materials")):
        material = _mapping(raw_material)
        if material.get("purpose") != "reference_ppt":
            continue
        link_id = str(material.get("link_id") or "")
        teacher_delete = material.get("teacher_intent") == "candidate_delete"
        units = [_mapping(item) for item in _list(material.get("units"))]
        tail_start = max(1, int(len(units) * tail_ratio) + 1)
        for position, unit in enumerate(units, start=1):
            unit_index = unit.get("unit_index")
            if not link_id or not isinstance(unit_index, int):
                continue
            slide_ref = f"material:{link_id}:unit:{unit_index}"
            text = str(unit.get("text") or "")
            role = _classify_slide(text, str(unit.get("title") or ""))
            is_trim_candidate = role == "practice" or (
                role == "example" and not preserve_examples
            )
            is_tail_practice = is_trim_candidate and position >= tail_start
            delete = (
                teacher_delete
                or (
                    trim_enabled
                    and is_tail_practice
                    and (
                        not prefer_short
                        or len(_compact_text(text)) > threshold
                    )
                )
            )
            textbook_refs = _best_textbook_refs(text, textbook_units)
            citations = [slide_ref, *textbook_refs]
            result.append(
                {
                    "slide_ref": slide_ref,
                    "role": role,
                    "action": "delete" if delete else "keep",
                    "delete_object_refs": [],
                    "textbook_refs": textbook_refs,
                    "reason": (
                        "教师已把该参考课件范围标为候选删除。"
                        if teacher_delete
                        else "课件后段练习题干较多，按本次精简偏好列为候选删除。"
                        if delete
                        else (
                            "讲授过程中的例题按教师偏好保留。"
                            if role == "example"
                            else "保留当前讲授结构，等待教师逐页审核。"
                        )
                    ),
                    "citations": citations,
                }
            )
    return result


def _slide_adaptations(
    value: object,
    pack: ResourcePackVersion,
    allowed_refs: set[str],
) -> list[dict[str, object]]:
    items = _object_list(value, maximum=2_000)
    ppt_refs = set(_material_references(pack.payload, "reference_ppt"))
    textbook_refs = set(_material_references(pack.payload, "textbook"))
    object_refs_by_slide = _safe_object_refs_by_slide(pack.payload)
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        slide_ref = str(item.get("slide_ref") or "")
        if slide_ref not in ppt_refs or slide_ref in seen:
            raise TeachingPrepValidationError(
                "slide adaptation refers to an unknown or repeated slide"
            )
        seen.add(slide_ref)
        role = str(item.get("role") or "")
        if role not in _SLIDE_ROLES:
            raise TeachingPrepValidationError(
                "slide adaptation role is invalid"
            )
        action = str(item.get("action") or "")
        if action not in {"keep", "delete"}:
            raise TeachingPrepValidationError(
                "slide adaptation action is invalid"
            )
        delete_object_refs = _strings(
            item.get("delete_object_refs", []),
            maximum=100,
            item_maximum=500,
        )
        if action != "keep" and delete_object_refs:
            raise TeachingPrepValidationError(
                "objects cannot be deleted from a deleted slide"
            )
        if any(
            ref not in object_refs_by_slide.get(slide_ref, set())
            for ref in delete_object_refs
        ):
            raise TeachingPrepValidationError(
                "slide adaptation refers to an unsafe or unknown object"
            )
        mapped_textbooks = _strings(
            item.get("textbook_refs"),
            maximum=6,
            item_maximum=300,
        )
        if any(ref not in textbook_refs for ref in mapped_textbooks):
            raise TeachingPrepValidationError(
                "slide adaptation refers to an unknown textbook page"
            )
        citations = _citations(item.get("citations"), allowed_refs)
        if slide_ref not in citations or any(
            ref not in citations for ref in mapped_textbooks
        ):
            raise TeachingPrepValidationError(
                "slide adaptation citations do not support its mapping"
            )
        result.append(
            {
                "slide_ref": slide_ref,
                "role": role,
                "action": action,
                "delete_object_refs": delete_object_refs,
                "textbook_refs": mapped_textbooks,
                "reason": _text(item.get("reason"), maximum=1_000),
                "citations": citations,
            }
        )
    if items and seen != ppt_refs:
        raise TeachingPrepValidationError(
            "slide adaptations must cover every frozen reference slide"
        )
    return result


def _material_units(
    payload: Mapping[str, object],
    purpose: str,
) -> list[tuple[str, dict[str, object]]]:
    result: list[tuple[str, dict[str, object]]] = []
    for raw_material in _list(payload.get("materials")):
        material = _mapping(raw_material)
        if material.get("purpose") != purpose:
            continue
        link_id = str(material.get("link_id") or "")
        for raw_unit in _list(material.get("units")):
            unit = _mapping(raw_unit)
            unit_index = unit.get("unit_index")
            if link_id and isinstance(unit_index, int):
                result.append(
                    (f"material:{link_id}:unit:{unit_index}", unit)
                )
    return result


def _best_textbook_refs(
    slide_text: str,
    textbook_units: list[tuple[str, dict[str, object]]],
) -> list[str]:
    slide_tokens = _bigrams(slide_text)
    if not slide_tokens:
        return []
    scored: list[tuple[int, str]] = []
    for ref, unit in textbook_units:
        common = len(slide_tokens & _bigrams(str(unit.get("text") or "")))
        if common >= 3:
            scored.append((common, ref))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [ref for _score, ref in scored[:2]]


def _classify_slide(text: str, title: str) -> str:
    value = f"{title}\n{text}"
    if re.search(
        r"典型例题|(?:^|[\s：:（(])例"
        r"(?:题|\s*\d{1,3}(?=\s|[：:、.．)）]|$))",
        value,
    ):
        return "example"
    if re.search(r"练习|巩固|检测|训练|做一做|试一试", value):
        return "practice"
    if re.search(r"小结|总结|回顾", value):
        return "summary"
    if re.search(r"情境|导入|问题提出", value):
        return "introduction"
    return "explanation"


def _bigrams(value: str) -> set[str]:
    compact = _compact_text(value)
    return {
        compact[index : index + 2]
        for index in range(max(0, len(compact) - 1))
        if len(compact[index : index + 2]) == 2
    }


def _compact_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def calculate_capacity(
    pack: ResourcePackVersion,
    draft: Mapping[str, object],
) -> dict[str, object]:
    lesson = _mapping(pack.payload.get("lesson"))
    raw_duration = lesson.get("duration_minutes")
    lesson_minutes = (
        int(raw_duration)
        if isinstance(raw_duration, int) and 20 <= raw_duration <= 180
        else 45
    )
    flow_items = _list(draft.get("lesson_flow"))
    flow_breakdown: list[dict[str, object]] = []
    flow_minutes = 0
    for raw in flow_items:
        item = _mapping(raw)
        phase = str(item["phase"])
        requested = int(item["suggested_minutes"])
        local_minutes = max(_PHASE_MINUTES[phase], requested)
        flow_minutes += local_minutes
        flow_breakdown.append(
            {
                "phase": phase,
                "label": _PHASE_LABELS[phase],
                "minimum_minutes": _PHASE_MINUTES[phase],
                "suggested_minutes": requested,
                "planned_minutes": local_minutes,
            }
        )
    recommendations = _list(draft.get("exercise_recommendations"))
    included = [
        _mapping(item)
        for item in recommendations
        if _mapping(item).get("action") == "include"
    ]
    exercise_minutes = sum(
        int(item["estimated_minutes"])
        for item in included
    )
    buffer_minutes = max(2, round(lesson_minutes * 0.05))
    planned_minutes = flow_minutes + exercise_minutes + buffer_minutes
    overrun = max(0, planned_minutes - lesson_minutes)
    reduction_options: list[dict[str, object]] = []
    if overrun:
        remaining = overrun
        for item in reversed(included):
            minutes = int(item["estimated_minutes"])
            reduction_options.append(
                {
                    "source_ref": str(item["source_ref"]),
                    "suggestion": "move_after_class",
                    "minutes_saved": minutes,
                }
            )
            remaining -= minutes
            if remaining <= 0:
                break
        if remaining > 0:
            reduction_options.append(
                {
                    "phase": "exploration",
                    "suggestion": "replace_shorter",
                    "minutes_saved": remaining,
                }
            )
    return {
        "lesson_minutes": lesson_minutes,
        "duration_source": (
            "resource_pack"
            if isinstance(raw_duration, int) and 20 <= raw_duration <= 180
            else "default_45_minutes"
        ),
        "flow_breakdown": flow_breakdown,
        "flow_minutes": flow_minutes,
        "exercise_minutes": exercise_minutes,
        "buffer_minutes": buffer_minutes,
        "planned_minutes": planned_minutes,
        "overrun_minutes": overrun,
        "within_capacity": overrun == 0,
        "reduction_options": reduction_options,
    }


def reference_catalog(
    pack: ResourcePackVersion,
) -> dict[str, str]:
    payload = pack.payload
    lesson = _mapping(payload.get("lesson"))
    lesson_id = str(lesson.get("lesson_node_id") or pack.lesson_node_id)
    result = {
        f"lesson:{lesson_id}": f"课时：{lesson.get('title') or '本课'}"
    }
    classroom = _mapping(payload.get("classroom"))
    if classroom.get("teacher_context"):
        result["teacher_context"] = "教师填写的班级整体情况"
    for raw_material in _list(payload.get("materials")):
        material = _mapping(raw_material)
        link_id = str(material.get("link_id") or "")
        name = str(material.get("material_name") or "资料")
        for raw_unit in _list(material.get("units")):
            unit = _mapping(raw_unit)
            index = unit.get("unit_index")
            if link_id and isinstance(index, int):
                result[
                    f"material:{link_id}:unit:{index}"
                ] = f"{name} · 第 {index} 页/张"
    for source_ref, item in _exercise_sources(payload):
        label = (
            item.get("content_label")
            or item.get("question_number")
            or "候选题"
        )
        result[source_ref] = f"教辅候选题：{label}"
    for source_ref, item in _question_sources(payload):
        label = item.get("question_number") or item.get("question_id")
        result[source_ref] = f"题库题：{label}"
    evidence = _mapping(payload.get("evidence"))
    assessment = _mapping(evidence.get("assessment"))
    for raw in _list(assessment.get("assessments")):
        item = _mapping(raw)
        assessment_id = item.get("assessment_id")
        if assessment_id is not None:
            result[
                f"assessment:{assessment_id}"
            ] = f"匿名班级汇总：{item.get('title') or assessment_id}"
    for raw in _list(payload.get("prior_reviews")):
        item = _mapping(raw)
        review_id = item.get("review_id")
        if review_id is not None:
            result[
                f"post_review:{review_id}"
            ] = "教师选择的本班课后复盘"
    return result


def _claims(
    value: object,
    allowed_refs: set[str],
    *,
    maximum: int,
) -> list[dict[str, object]]:
    items = _object_list(value, maximum=maximum)
    if not items:
        raise TeachingPrepValidationError("draft claim list cannot be empty")
    return [
        {
            "text": _text(item.get("text"), maximum=1_000),
            "citations": _citations(item.get("citations"), allowed_refs),
        }
        for item in items
    ]


def _focus_points(
    value: object,
    allowed_refs: set[str],
) -> list[dict[str, object]]:
    items = _object_list(value, maximum=20)
    if not items:
        raise TeachingPrepValidationError("focus point list cannot be empty")
    result = []
    for item in items:
        kind = str(item.get("kind") or "")
        if kind not in {"key", "difficulty"}:
            raise TeachingPrepValidationError("focus point kind is invalid")
        result.append(
            {
                "kind": kind,
                "title": _text(item.get("title"), maximum=300),
                "rationale": _text(item.get("rationale"), maximum=1_000),
                "citations": _citations(
                    item.get("citations"),
                    allowed_refs,
                ),
            }
        )
    return result


def _flow(
    value: object,
    allowed_refs: set[str],
) -> list[dict[str, object]]:
    items = _object_list(value, maximum=len(_PHASE_MINUTES))
    phases = [str(item.get("phase") or "") for item in items]
    if set(phases) != set(_PHASE_MINUTES) or len(phases) != len(set(phases)):
        raise TeachingPrepValidationError(
            "lesson flow must contain every required phase once"
        )
    return [
        {
            "phase": str(item["phase"]),
            "title": _text(item.get("title"), maximum=200),
            "purpose": _text(item.get("purpose"), maximum=800),
            "suggested_minutes": _bounded_int(
                item.get("suggested_minutes"),
                minimum=0,
                maximum=120,
            ),
            "citations": _citations(
                item.get("citations"),
                allowed_refs,
            ),
        }
        for item in items
    ]


def _recommendations(
    value: object,
    allowed_refs: set[str],
    allowed_exercises: set[str],
    allowed_slides: set[str],
) -> list[dict[str, object]]:
    items = _object_list(value, maximum=30)
    result = []
    included = 0
    for item in items:
        source_ref = str(item.get("source_ref") or "")
        if source_ref not in allowed_exercises:
            raise TeachingPrepValidationError(
                "exercise recommendation refers to an unknown question"
            )
        action = str(item.get("action") or "")
        if action not in _RECOMMENDATION_ACTIONS:
            raise TeachingPrepValidationError(
                "exercise recommendation action is invalid"
            )
        if action == "include":
            included += 1
        raw_target = item.get("target_slide_ref")
        target_slide_ref = (
            str(raw_target).strip() if raw_target is not None else None
        )
        if target_slide_ref == "":
            target_slide_ref = None
        if target_slide_ref is not None and target_slide_ref not in allowed_slides:
            raise TeachingPrepValidationError(
                "exercise recommendation targets an unknown slide"
            )
        result.append(
            {
                "source_ref": source_ref,
                "action": action,
                "target_slide_ref": (
                    target_slide_ref if action == "include" else None
                ),
                "title": _text(item.get("title"), maximum=300),
                "reason": _text(item.get("reason"), maximum=1_000),
                "estimated_minutes": _bounded_int(
                    item.get("estimated_minutes"),
                    minimum=1,
                    maximum=60,
                ),
                "citations": _citations(
                    item.get("citations"),
                    allowed_refs,
                ),
            }
        )
    if included > 3:
        raise TeachingPrepValidationError(
            "at most three questions may be included in class"
        )
    return result


def _preferred_insertion_slide(
    adaptations: list[dict[str, object]],
) -> str | None:
    kept = [
        item
        for item in adaptations
        if item.get("action") == "keep"
    ]
    for role in ("practice", "example", "explanation"):
        matching = [item for item in kept if item.get("role") == role]
        if matching:
            return str(matching[-1].get("slide_ref") or "") or None
    return str(kept[-1].get("slide_ref") or "") if kept else None


def _safe_object_refs_by_slide(
    payload: Mapping[str, object],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for slide_ref, unit in _material_units(payload, "reference_ppt"):
        summary = _mapping(unit.get("object_summary"))
        refs = set()
        for raw_object in _list(summary.get("objects")):
            item = _mapping(raw_object)
            object_ref = str(item.get("object_ref") or "")
            if object_ref and item.get("safe_to_delete") is True:
                refs.add(f"{slide_ref}:object:{object_ref}")
        result[slide_ref] = refs
    return result


def _citations(
    value: object,
    allowed_refs: set[str],
) -> list[str]:
    citations = _strings(value, maximum=20, item_maximum=300)
    if not citations:
        raise TeachingPrepValidationError("draft item requires a citation")
    if any(item not in allowed_refs for item in citations):
        raise TeachingPrepValidationError(
            "draft contains an unknown source citation"
        )
    return citations


def _validate_explicit_locators(
    draft: Mapping[str, object],
    pack: ResourcePackVersion,
) -> None:
    question_numbers: dict[str, set[str]] = {}
    for source_ref, item in (
        _exercise_sources(pack.payload) + _question_sources(pack.payload)
    ):
        raw = item.get("question_number")
        if raw is not None:
            question_numbers[source_ref] = {str(raw).strip()}
    for section in (
        "knowledge_objectives",
        "focus_points",
        "anticipated_difficulties",
        "lesson_flow",
        "exercise_recommendations",
    ):
        for raw in _list(draft.get(section)):
            item = _mapping(raw)
            citations = {
                str(value)
                for value in _list(item.get("citations"))
            }
            text = " ".join(
                str(item.get(field) or "")
                for field in ("text", "title", "rationale", "purpose", "reason")
            )
            for match in _EXPLICIT_LOCATOR.finditer(text):
                number = match.group("number")
                kind = match.group("kind")
                if kind in {"页", "张"}:
                    allowed = {
                        citation.rsplit(":unit:", 1)[1]
                        for citation in citations
                        if ":unit:" in citation
                    }
                else:
                    allowed = set().union(
                        *(
                            question_numbers.get(citation, set())
                            for citation in citations
                        ),
                        set(),
                    )
                if number not in allowed:
                    raise TeachingPrepValidationError(
                        "draft contains an unsupported page or question number"
                    )


def _material_references(
    payload: Mapping[str, object],
    purpose: str,
) -> list[str]:
    result = []
    for raw in _list(payload.get("materials")):
        material = _mapping(raw)
        if material.get("purpose") != purpose:
            continue
        link_id = str(material.get("link_id") or "")
        for unit in _list(material.get("units")):
            item = _mapping(unit)
            if link_id and isinstance(item.get("unit_index"), int):
                result.append(
                    f"material:{link_id}:unit:{item['unit_index']}"
                )
    return result


def _rank_supplement_sources(
    payload: Mapping[str, object],
    exercise_sources: list[tuple[str, dict[str, object]]],
    question_sources: list[tuple[str, dict[str, object]]],
    preferences: Mapping[str, object],
) -> list[tuple[str, dict[str, object], bool, str | None]]:
    avoid_homework_copy = bool(
        preferences.get("avoid_direct_homework_copy", True)
    )
    prioritize_homework = bool(
        preferences.get("prioritize_homework_workbook", True)
    )
    avoid_duplicates = bool(
        preferences.get("avoid_ppt_duplicates", True)
    )
    ppt_text = "\n".join(
        str(unit.get("text") or "")
        for _ref, unit in _material_units(payload, "reference_ppt")
    )
    ranked: list[
        tuple[int, int, str, dict[str, object], bool, str | None]
    ] = []
    for order, (source_ref, item) in enumerate(exercise_sources):
        homework_source = _is_homework_workbook(item)
        duplicate = avoid_duplicates and _duplicates_reference_ppt(
            item,
            ppt_text,
        )
        eligible = not (avoid_homework_copy and homework_source) and not duplicate
        reason = None
        if avoid_homework_copy and homework_source:
            reason = "重点参考作业教辅的题型，但不直接照搬学生作业原题。"
        elif duplicate:
            reason = "与原课件内容重复，按本次偏好保留为备用而不再补入。"
        priority = (
            0
            if prioritize_homework and homework_source and not avoid_homework_copy
            else 1
        )
        ranked.append(
            (priority, order, source_ref, item, eligible, reason)
        )
    question_offset = len(exercise_sources)
    for order, (source_ref, item) in enumerate(question_sources):
        ranked.append(
            (
                2,
                question_offset + order,
                source_ref,
                item,
                True,
                None,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        (source_ref, item, eligible, reason)
        for _priority, _order, source_ref, item, eligible, reason in ranked
    ]


def _is_homework_workbook(item: Mapping[str, object]) -> bool:
    roles = {
        str(region.get("semester_material_role") or "")
        for region in _list(item.get("question_regions"))
        if isinstance(region, Mapping)
    }
    return "homework_workbook" in roles


def _duplicates_reference_ppt(
    item: Mapping[str, object],
    ppt_text: str,
) -> bool:
    label = _compact_text(str(item.get("content_label") or ""))
    compact_ppt = _compact_text(ppt_text)
    return len(label) >= 6 and label in compact_ppt


def _exercise_sources(
    payload: Mapping[str, object],
) -> list[tuple[str, dict[str, object]]]:
    result = []
    for raw in _list(payload.get("exercises")):
        item = _mapping(raw)
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id and item.get("selection_status") != "excluded":
            result.append((f"exercise:{candidate_id}", item))
    return result


def _question_sources(
    payload: Mapping[str, object],
) -> list[tuple[str, dict[str, object]]]:
    evidence = _mapping(payload.get("evidence"))
    question = _mapping(evidence.get("question"))
    result = []
    for raw in _list(question.get("items")):
        item = _mapping(raw)
        question_id = item.get("question_id")
        if question_id is not None:
            result.append((f"question:{question_id}", item))
    return result


def _uncertainties(payload: Mapping[str, object]) -> list[str]:
    result = []
    for raw in _list(payload.get("missing_and_uncertain"))[:30]:
        if isinstance(raw, Mapping):
            code = str(raw.get("code") or "待确认项")
            result.append(f"资源包待确认：{code}")
        elif raw:
            result.append(f"资源包待确认：{raw}")
    return result


def _positive_minutes(value: object, *, default: int) -> int:
    if isinstance(value, int) and 1 <= value <= 60:
        return value
    return default


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TeachingPrepValidationError("draft minute value is invalid")
    if not minimum <= value <= maximum:
        raise TeachingPrepValidationError("draft minute value is invalid")
    return value


def _text(value: object, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise TeachingPrepValidationError("draft text is invalid")
    return text


def _strings(
    value: object,
    *,
    maximum: int,
    item_maximum: int,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TeachingPrepValidationError("draft list is invalid")
    return [_text(item, maximum=item_maximum) for item in value]


def _object_list(
    value: object,
    *,
    maximum: int,
) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TeachingPrepValidationError("draft object list is invalid")
    if any(not isinstance(item, Mapping) for item in value):
        raise TeachingPrepValidationError("draft object list is invalid")
    return [dict(item) for item in value]


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


__all__ = [
    "build_local_template",
    "calculate_capacity",
    "draft_preflight",
    "reference_catalog",
    "validate_draft_payload",
]
