from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.domain.models import (
    LessonDraftVersion,
    ResourcePackVersion,
)


_AUTOMATIC_KINDS = {
    "delete_slide",
    "reorder_slide",
    "delete_shape",
    "add_text_box",
    "add_slide",
    "insert_static_image",
    "move_static_image",
    "scale_static_image",
    "crop_static_image",
    "replace_static_image",
}
_NOOP_KINDS = {"keep_slide"}
_MANUAL_ONLY_KINDS = {
    "hide_slide",
    "copy_slide",
    "modify_text_box",
    "manual_note",
}
_ALL_KINDS = _AUTOMATIC_KINDS | _NOOP_KINDS | _MANUAL_ONLY_KINDS
_DECISIONS = {"proposed", "approved", "rejected"}
_RISKS = {"low", "medium", "high", "blocked"}
_SAFE_EXISTING_OBJECT_TYPES = {"shape", "text_box", "line"}
_PROTECTED_OBJECT_TYPES = {
    "graphicFrame": "公式、图表或嵌入对象",
    "grpSp": "组合图形",
    "oleObj": "嵌入对象",
    "video": "视频",
    "audio": "音频",
    "control": "控件或宏对象",
}


def build_slide_plan_payload(
    pack: ResourcePackVersion,
    draft: LessonDraftVersion,
) -> tuple[dict[str, object], str]:
    presentations = _reference_presentations(pack.payload)
    if not presentations:
        raise TeachingPrepValidationError(
            "confirmed reference PPT is required for a slide plan"
        )
    slides: list[dict[str, object]] = []
    unsupported: list[dict[str, object]] = []
    for presentation in presentations:
        for unit in presentation["units"]:
            slide = _slide(
                presentation=presentation,
                unit=unit,
            )
            slides.append(slide)
            unsupported.extend(_unsupported(slide, unit))
    if not slides:
        raise TeachingPrepValidationError(
            "reference PPT has no frozen slide units"
        )
    operations: list[dict[str, object]] = []
    for slide in slides:
        presentation = next(
            item
            for item in presentations
            if item["link_id"] == slide["source_link_id"]
        )
        kind = (
            "delete_slide"
            if presentation["teacher_intent"] == "candidate_delete"
            else "keep_slide"
        )
        operations.append(
            _operation(
                draft.id,
                kind=kind,
                target_key=str(slide["stable_signature"]),
                target={
                    "target_kind": "slide",
                    "slide_signature": slide["stable_signature"],
                    "generated_page_number": slide["original_index"],
                    "material_unit_id": slide["material_unit_id"],
                    "wps_object_id": None,
                    "object_type": None,
                    "position": None,
                    "content_summary": slide["title"],
                    "match_strategy": "source_fingerprint_and_slide_signature",
                },
                reason=(
                    "教师在资源包中标记为候选删除。"
                    if kind == "delete_slide"
                    else "保留教师确认的参考课件新授流程。"
                ),
                citations=[
                    (
                        f"material:{slide['source_link_id']}:"
                        f"unit:{slide['original_index']}"
                    )
                ],
                planned_minutes=0,
                risk="low",
                execution_mode="automatic" if kind != "keep_slide" else "noop",
                support_note=None,
            )
        )
    draft_recommendations = _list(
        draft.payload.get("exercise_recommendations")
    )
    exercise_map = {
        f"exercise:{item.get('candidate_id')}": item
        for item in _object_list(pack.payload.get("exercises"))
        if item.get("candidate_id")
    }
    question_map = {
        f"question:{item.get('question_id')}": item
        for item in _object_list(
            _mapping(
                _mapping(pack.payload.get("evidence")).get("question")
            ).get("items")
        )
        if item.get("question_id") is not None
    }
    last_signature = str(slides[-1]["stable_signature"])
    insert_position = len(slides) + 1
    for raw in draft_recommendations:
        recommendation = _mapping(raw)
        if recommendation.get("action") != "include":
            continue
        source_ref = str(recommendation.get("source_ref") or "")
        if source_ref in exercise_map:
            exercise = exercise_map[source_ref]
            regions = _object_list(exercise.get("question_regions"))
            if not regions:
                continue
            region = regions[0]
            add_id = _operation_id(
                draft.id,
                "add_slide",
                f"{source_ref}:{insert_position}",
            )
            operations.append(
                _operation(
                    draft.id,
                    operation_id=add_id,
                    kind="add_slide",
                    target_key=f"{source_ref}:{insert_position}",
                    target={
                        "target_kind": "new_slide",
                        "slide_signature": None,
                        "generated_page_number": insert_position,
                        "material_unit_id": None,
                        "wps_object_id": None,
                        "object_type": "slide",
                        "position": {"index": insert_position},
                        "content_summary": recommendation.get("title"),
                        "match_strategy": "new_object",
                    },
                    reason=str(recommendation.get("reason") or "加入关键课堂题。"),
                    citations=_strings(
                        recommendation.get("citations"),
                        maximum=20,
                    ),
                    planned_minutes=_minutes(
                        recommendation.get("estimated_minutes"),
                        default=4,
                    ),
                    risk="low",
                    execution_mode="automatic",
                    support_note="在现有版式上新增普通页面。",
                    details={
                        "insert_after_signature": last_signature,
                        "layout_source_signature": last_signature,
                    },
                )
            )
            operations.append(
                _operation(
                    draft.id,
                    kind="insert_static_image",
                    target_key=f"{source_ref}:image:{insert_position}",
                    target={
                        "target_kind": "new_object",
                        "slide_signature": None,
                        "new_slide_operation_id": add_id,
                        "generated_page_number": insert_position,
                        "material_unit_id": region.get("material_unit_id"),
                        "wps_object_id": None,
                        "object_type": "static_image",
                        "position": {
                            "x": 0.08,
                            "y": 0.14,
                            "width": 0.84,
                            "height": 0.72,
                        },
                        "content_summary": recommendation.get("title"),
                        "match_strategy": "new_object",
                    },
                    reason="插入教师已确认范围内的题目裁图。",
                    citations=[source_ref],
                    planned_minutes=0,
                    risk="low",
                    execution_mode="automatic",
                    support_note="静态图片来源已冻结，未绑定对象动画。",
                    details={
                        "asset_ref": region.get("preview_url"),
                        "asset_source_sha256": region.get(
                            "source_version_sha256"
                        ),
                        "has_object_animation": False,
                    },
                )
            )
            insert_position += 1
        elif source_ref in question_map:
            operations.append(
                _operation(
                    draft.id,
                    kind="manual_note",
                    target_key=source_ref,
                    target={
                        "target_kind": "manual",
                        "slide_signature": None,
                        "generated_page_number": None,
                        "material_unit_id": None,
                        "wps_object_id": None,
                        "object_type": None,
                        "position": None,
                        "content_summary": recommendation.get("title"),
                        "match_strategy": "manual_only",
                    },
                    reason=(
                        "题库题没有冻结的课件裁图，先列入人工插入清单。"
                    ),
                    citations=[source_ref],
                    planned_minutes=_minutes(
                        recommendation.get("estimated_minutes"),
                        default=4,
                    ),
                    risk="blocked",
                    execution_mode="manual_only",
                    support_note="不能从题目正文直接生成可执行 WPS 操作。",
                )
            )
    source_state = _digest(
        [
            {
                "link_id": item["link_id"],
                "material_version_id": item["material_version_id"],
                "content_sha256": item["content_sha256"],
                "slides": [
                    slide["stable_signature"]
                    for slide in slides
                    if slide["source_link_id"] == item["link_id"]
                ],
            }
            for item in presentations
        ]
    )
    return (
        {
            "schema_version": 1,
            "source_presentations": [
                {
                    key: item[key]
                    for key in (
                        "link_id",
                        "material_version_id",
                        "material_name",
                        "content_sha256",
                        "teacher_intent",
                    )
                }
                for item in presentations
            ],
            "slides": slides,
            "operations": operations,
            "unsupported_objects": unsupported,
            "approval_history": [],
        },
        source_state,
    )


def validate_plan_payload(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise TeachingPrepValidationError("slide plan must be an object")
    required = {
        "schema_version",
        "source_presentations",
        "slides",
        "operations",
        "unsupported_objects",
        "approval_history",
    }
    if set(payload) != required or payload.get("schema_version") != 1:
        raise TeachingPrepValidationError("slide plan structure is invalid")
    operations = [
        validate_operation(item)
        for item in _object_list(payload.get("operations"), maximum=1_000)
    ]
    operation_ids = [str(item["operation_id"]) for item in operations]
    if len(operation_ids) != len(set(operation_ids)):
        raise TeachingPrepValidationError(
            "slide plan operation IDs must be unique"
        )
    return {
        "schema_version": 1,
        "source_presentations": _object_list(
            payload.get("source_presentations"),
            maximum=50,
        ),
        "slides": _object_list(payload.get("slides"), maximum=2_000),
        "operations": operations,
        "unsupported_objects": _object_list(
            payload.get("unsupported_objects"),
            maximum=2_000,
        ),
        "approval_history": _object_list(
            payload.get("approval_history"),
            maximum=10_000,
        ),
    }


def validate_operation(value: object) -> dict[str, object]:
    item = _mapping(value)
    required = {
        "operation_id",
        "kind",
        "decision",
        "target",
        "reason",
        "citations",
        "planned_minutes",
        "risk",
        "execution_mode",
        "support_note",
        "details",
        "teacher_note",
    }
    if set(item) != required:
        raise TeachingPrepValidationError(
            "slide operation structure is invalid"
        )
    kind = str(item.get("kind") or "")
    if kind not in _ALL_KINDS:
        raise TeachingPrepValidationError("slide operation kind is invalid")
    decision = str(item.get("decision") or "")
    if decision not in _DECISIONS:
        raise TeachingPrepValidationError(
            "slide operation decision is invalid"
        )
    risk = str(item.get("risk") or "")
    if risk not in _RISKS:
        raise TeachingPrepValidationError("slide operation risk is invalid")
    execution_mode = str(item.get("execution_mode") or "")
    expected_mode = (
        "automatic"
        if kind in _AUTOMATIC_KINDS
        else "noop"
        if kind in _NOOP_KINDS
        else "manual_only"
    )
    if execution_mode != expected_mode:
        raise TeachingPrepValidationError(
            "slide operation execution mode is invalid"
        )
    target = _mapping(item.get("target"))
    if not target:
        raise TeachingPrepValidationError(
            "slide operation target is required"
        )
    result = {
        "operation_id": _bounded_text(
            item.get("operation_id"),
            maximum=64,
        ),
        "kind": kind,
        "decision": decision,
        "target": target,
        "reason": _bounded_text(item.get("reason"), maximum=1_000),
        "citations": _strings(item.get("citations"), maximum=20),
        "planned_minutes": _minutes(
            item.get("planned_minutes"),
            default=0,
            minimum=0,
        ),
        "risk": risk,
        "execution_mode": execution_mode,
        "support_note": _optional_text(
            item.get("support_note"),
            maximum=500,
        ),
        "details": _mapping(item.get("details")),
        "teacher_note": _optional_text(
            item.get("teacher_note"),
            maximum=1_000,
        ),
    }
    if decision == "approved":
        require_approval_allowed(result)
    return result


def require_approval_allowed(operation: Mapping[str, object]) -> None:
    kind = str(operation.get("kind") or "")
    if kind in _NOOP_KINDS:
        return
    if kind not in _AUTOMATIC_KINDS:
        raise TeachingPrepValidationError(
            "operation is outside the A00 automatic whitelist"
        )
    target = _mapping(operation.get("target"))
    details = _mapping(operation.get("details"))
    if kind in {
        "insert_static_image",
        "move_static_image",
        "scale_static_image",
        "crop_static_image",
        "replace_static_image",
    } and details.get("has_object_animation") is not False:
        raise TeachingPrepValidationError(
            "animated image operation cannot be approved"
        )
    if kind in {
        "delete_shape",
        "move_static_image",
        "scale_static_image",
        "crop_static_image",
        "replace_static_image",
    }:
        locator = _mapping(target.get("object_locator"))
        object_type = str(target.get("object_type") or "")
        if (
            locator.get("match_confidence") != "exact"
            or not (
                locator.get("wps_object_id")
                or locator.get("stable_signature")
            )
        ):
            raise TeachingPrepValidationError(
                "existing object operation lacks a stable locator"
            )
        if (
            kind == "delete_shape"
            and object_type not in _SAFE_EXISTING_OBJECT_TYPES
        ):
            raise TeachingPrepValidationError(
                "protected object cannot be approved for deletion"
            )


def diff_preview(
    payload: Mapping[str, object],
    *,
    include_proposed: bool,
    source_changed: bool,
) -> dict[str, object]:
    before = deepcopy(_object_list(payload.get("slides")))
    after = [
        {
            "stable_signature": item.get("stable_signature"),
            "title": item.get("title"),
            "preview_url": item.get("preview_url"),
            "original_index": item.get("original_index"),
            "state": "kept",
        }
        for item in before
    ]
    changes: list[dict[str, object]] = []
    manual: list[dict[str, object]] = []
    for operation in _object_list(payload.get("operations")):
        decision = str(operation.get("decision") or "")
        if decision == "rejected":
            continue
        if decision == "proposed" and not include_proposed:
            continue
        kind = str(operation.get("kind") or "")
        target = _mapping(operation.get("target"))
        signature = target.get("slide_signature")
        if operation.get("execution_mode") == "manual_only":
            manual.append(
                {
                    "operation_id": operation.get("operation_id"),
                    "kind": kind,
                    "reason": operation.get("reason"),
                }
            )
            continue
        if kind == "delete_slide":
            after = [
                item
                for item in after
                if item.get("stable_signature") != signature
            ]
        elif kind == "hide_slide":
            for item in after:
                if item.get("stable_signature") == signature:
                    item["state"] = "hidden"
        elif kind == "reorder_slide":
            matching = [
                item
                for item in after
                if item.get("stable_signature") == signature
            ]
            if matching:
                after.remove(matching[0])
                position = int(
                    _mapping(operation.get("details")).get(
                        "target_position",
                        len(after) + 1,
                    )
                )
                after.insert(max(0, min(len(after), position - 1)), matching[0])
        elif kind in {"add_slide", "copy_slide"}:
            after.append(
                {
                    "stable_signature": (
                        f"new:{operation.get('operation_id')}"
                    ),
                    "title": target.get("content_summary") or "新增页",
                    "preview_url": None,
                    "original_index": None,
                    "state": "new",
                }
            )
        if kind != "keep_slide":
            changes.append(
                {
                    "operation_id": operation.get("operation_id"),
                    "kind": kind,
                    "decision": decision,
                    "target": target,
                    "reason": operation.get("reason"),
                }
            )
    return {
        "valid_for_execution": not source_changed,
        "source_changed": source_changed,
        "includes_proposed_operations": include_proposed,
        "before_slide_count": len(before),
        "after_slide_count": len(after),
        "before": before,
        "after": [
            {**item, "planned_index": index}
            for index, item in enumerate(after, start=1)
        ],
        "changes": changes,
        "manual_only": manual,
    }


def _reference_presentations(
    payload: Mapping[str, object],
) -> list[dict[str, object]]:
    result = []
    for item in _object_list(payload.get("materials")):
        if (
            item.get("purpose") == "reference_ppt"
            and item.get("material_type") == "pptx"
        ):
            result.append(item)
    return result


def _slide(
    *,
    presentation: Mapping[str, object],
    unit: Mapping[str, object],
) -> dict[str, object]:
    signature = _digest(
        {
            "content_sha256": presentation.get("content_sha256"),
            "link_id": presentation.get("link_id"),
            "material_unit_id": unit.get("unit_id"),
            "unit_index": unit.get("unit_index"),
            "title": unit.get("title"),
            "text": unit.get("text"),
            "object_summary": unit.get("object_summary"),
            "preview_sha256": unit.get("preview_sha256"),
        }
    )
    return {
        "source_link_id": presentation.get("link_id"),
        "material_version_id": presentation.get("material_version_id"),
        "material_name": presentation.get("material_name"),
        "source_ppt_sha256": presentation.get("content_sha256"),
        "material_unit_id": unit.get("unit_id"),
        "original_index": unit.get("unit_index"),
        "title": unit.get("title") or f"第 {unit.get('unit_index')} 页",
        "text_summary": str(unit.get("text") or "")[:240],
        "object_summary": _mapping(unit.get("object_summary")),
        "preview_url": unit.get("preview_url"),
        "stable_signature": signature,
        "wps_slide_id": None,
        "match_strategy": "source_fingerprint_and_slide_signature",
    }


def _unsupported(
    slide: Mapping[str, object],
    unit: Mapping[str, object],
) -> list[dict[str, object]]:
    result = []
    summary = _mapping(slide.get("object_summary"))
    types = _mapping(summary.get("object_types"))
    for object_type, label in _PROTECTED_OBJECT_TYPES.items():
        count = types.get(object_type)
        if isinstance(count, int) and count > 0:
            result.append(
                {
                    "slide_signature": slide.get("stable_signature"),
                    "generated_page_number": slide.get("original_index"),
                    "object_type": object_type,
                    "count": count,
                    "label": label,
                    "policy": "read_only_preserve",
                }
            )
    if unit.get("formula_review_required"):
        result.append(
            {
                "slide_signature": slide.get("stable_signature"),
                "generated_page_number": slide.get("original_index"),
                "object_type": "complex_math_text",
                "count": 1,
                "label": "数学混排文字",
                "policy": "manual_only",
            }
        )
    return result


def _operation(
    draft_id: str,
    *,
    kind: str,
    target_key: str,
    target: dict[str, object],
    reason: str,
    citations: Sequence[str],
    planned_minutes: int,
    risk: str,
    execution_mode: str,
    support_note: str | None,
    details: dict[str, object] | None = None,
    operation_id: str | None = None,
) -> dict[str, object]:
    clean_target = dict(target)
    clean_target["pre_execution_match"] = {
        "status": (
            "pending_a08"
            if clean_target.get("target_kind") in {"slide", "existing_object"}
            else "not_applicable"
        ),
        "must_fail_closed_on_conflict": True,
    }
    return {
        "operation_id": operation_id or _operation_id(
            draft_id,
            kind,
            target_key,
        ),
        "kind": kind,
        "decision": "proposed",
        "target": clean_target,
        "reason": reason,
        "citations": list(citations),
        "planned_minutes": planned_minutes,
        "risk": risk,
        "execution_mode": execution_mode,
        "support_note": support_note,
        "details": details or {},
        "teacher_note": None,
    }


def _operation_id(draft_id: str, kind: str, target: str) -> str:
    return hashlib.sha256(
        f"{draft_id}:{kind}:{target}".encode("utf-8")
    ).hexdigest()[:32]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _minutes(
    value: object,
    *,
    default: int,
    minimum: int = 1,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if not minimum <= value <= 120:
        raise TeachingPrepValidationError(
            "slide operation minute value is invalid"
        )
    return value


def _strings(value: object, *, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TeachingPrepValidationError("slide plan string list is invalid")
    return [
        _bounded_text(item, maximum=300)
        for item in value
    ]


def _bounded_text(value: object, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise TeachingPrepValidationError("slide plan text is invalid")
    return text


def _optional_text(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise TeachingPrepValidationError("slide plan text is invalid")
    return text


def _object_list(
    value: object,
    *,
    maximum: int = 10_000,
) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TeachingPrepValidationError("slide plan list is invalid")
    if any(not isinstance(item, Mapping) for item in value):
        raise TeachingPrepValidationError("slide plan list is invalid")
    return [dict(item) for item in value]


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


__all__ = [
    "build_slide_plan_payload",
    "diff_preview",
    "require_approval_allowed",
    "validate_operation",
    "validate_plan_payload",
]
