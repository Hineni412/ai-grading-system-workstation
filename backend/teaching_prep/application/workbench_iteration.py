from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.teaching_prep.domain.errors import TeachingPrepValidationError


_PURPOSES = {
    "textbook",
    "reference_ppt",
    "exercise",
    "answer",
    "supplement",
}
_PPT_INTENTS = {"keep", "candidate_delete"}
_DIFFICULTIES = {"unrated", "easy", "medium", "hard"}
_CLASSROOM_USES = {
    "introduction",
    "example",
    "guided_practice",
    "independent_practice",
    "diagnostic",
    "challenge",
    "summary",
}
_DIFFICULTY_ALIASES = {
    "未评定": "unrated",
    "容易": "easy",
    "简单": "easy",
    "中等": "medium",
    "困难": "hard",
    "较难": "hard",
}
_CLASSROOM_USE_ALIASES = {
    "导入": "introduction",
    "例题": "example",
    "课堂练习": "guided_practice",
    "引导练习": "guided_practice",
    "独立练习": "independent_practice",
    "课堂检测": "diagnostic",
    "诊断": "diagnostic",
    "挑战": "challenge",
    "小结": "summary",
    "总结": "summary",
}


def normalize_reference_selection(
    value: Mapping[str, object],
    *,
    catalog: Mapping[str, object],
) -> dict[str, object]:
    allowed = {
        "material_selections",
        "exercise_candidate_ids",
        "question_ids",
        "assessment_ids",
        "knowledge_scope",
        "preparation_preferences",
        "class_name",
        "teacher_context",
    }
    if set(value) - allowed:
        raise TeachingPrepValidationError(
            "reference selection has unsupported fields"
        )
    links = {
        str(item["link_id"]): item
        for item in _mapping_list(catalog.get("material_links"), "material_links")
    }
    selections: list[dict[str, object]] = []
    seen_links: set[str] = set()
    for raw in _mapping_list(
        value.get("material_selections", []), "material_selections"
    ):
        if set(raw) - {"link_id", "start_unit", "end_unit", "ppt_intent"}:
            raise TeachingPrepValidationError(
                "material selection has unsupported fields"
            )
        link_id = _id(raw.get("link_id"), "material link")
        link = links.get(link_id)
        if link is None or link_id in seen_links:
            raise TeachingPrepValidationError(
                "material selection refers to an unavailable or repeated link"
            )
        start = _positive_int(raw.get("start_unit"), "start_unit")
        end = _positive_int(raw.get("end_unit"), "end_unit")
        if (
            start < int(link["start_unit"])
            or end > int(link["end_unit"])
            or end < start
        ):
            raise TeachingPrepValidationError(
                "material selection range is outside the confirmed link"
            )
        purpose = str(link.get("purpose") or "")
        if purpose not in _PURPOSES:
            raise TeachingPrepValidationError(
                "material selection purpose is invalid"
            )
        item: dict[str, object] = {
            "link_id": link_id,
            "material_version_id": str(link["material_version_id"]),
            "purpose": purpose,
            "start_unit": start,
            "end_unit": end,
        }
        if purpose == "reference_ppt":
            intent = str(raw.get("ppt_intent") or "keep")
            if intent not in _PPT_INTENTS:
                raise TeachingPrepValidationError(
                    "reference PPT intent is invalid"
                )
            item["ppt_intent"] = intent
        selections.append(item)
        seen_links.add(link_id)
    if not selections:
        raise TeachingPrepValidationError(
            "select at least one confirmed reference range"
        )
    return {
        "material_selections": selections,
        "exercise_candidate_ids": _ids(
            value.get("exercise_candidate_ids", []),
            "exercise_candidate_ids",
            maximum=200,
        ),
        "question_ids": _positive_ids(
            value.get("question_ids", []), "question_ids", maximum=500
        ),
        "assessment_ids": _positive_ids(
            value.get("assessment_ids", []), "assessment_ids", maximum=50
        ),
        "knowledge_scope": _strings(
            value.get("knowledge_scope", []),
            "knowledge_scope",
            maximum=100,
            item_maximum=160,
        ),
        "preparation_preferences": _mapping(
            value.get("preparation_preferences", {}),
            "preparation_preferences",
        ),
        "class_name": _optional_text(
            value.get("class_name"), "class_name", maximum=120
        ),
        "teacher_context": _optional_text(
            value.get("teacher_context"), "teacher_context", maximum=2_000
        ),
    }


def snapshot_model_payload(
    selection: Mapping[str, object],
    *,
    catalog: Mapping[str, object],
) -> dict[str, object]:
    selected = {
        str(item["link_id"]): item
        for item in _mapping_list(
            selection.get("material_selections"), "material_selections"
        )
    }
    materials: list[dict[str, object]] = []
    for raw_link in _mapping_list(catalog.get("material_links"), "material_links"):
        link_id = str(raw_link["link_id"])
        chosen = selected.get(link_id)
        if chosen is None:
            continue
        start = int(chosen["start_unit"])
        end = int(chosen["end_unit"])
        materials.append(
            {
                "link_id": link_id,
                "purpose": str(raw_link["purpose"]),
                "material_version_id": str(raw_link["material_version_id"]),
                "material_name": str(raw_link["material_name"]),
                "material_type": str(raw_link["material_type"]),
                "content_sha256": str(raw_link["content_sha256"]),
                "start_unit": start,
                "end_unit": end,
                "units": [
                    dict(unit)
                    for unit in _mapping_list(raw_link.get("units"), "units")
                    if start <= int(unit["unit_index"]) <= end
                ],
            }
        )
    lesson = _mapping(catalog.get("lesson"), "lesson")
    return {
        "schema_version": 1,
        "lesson": dict(lesson),
        "materials": materials,
        "teacher_context": selection.get("teacher_context"),
        "preparation_preferences": dict(
            _mapping(
                selection.get("preparation_preferences", {}),
                "preparation_preferences",
            )
        ),
    }


def normalize_exercise_suggestion_payload(
    value: object,
    *,
    snapshot: Mapping[str, object],
) -> list[dict[str, object]]:
    root = _mapping(value, "exercise suggestion response")
    if set(root) != {"suggestions"}:
        raise TeachingPrepValidationError(
            "exercise suggestion response has unsupported fields"
        )
    materials = {
        str(item["material_version_id"]): item
        for item in _mapping_list(snapshot.get("materials"), "materials")
        if str(item.get("purpose") or "") == "exercise"
    }
    allowed_units = {
        str(unit["unit_id"]): (
            str(material["material_version_id"]),
            int(unit["unit_index"]),
        )
        for material in materials.values()
        for unit in _mapping_list(material.get("units"), "units")
    }
    result: list[dict[str, object]] = []
    raw_suggestions = _mapping_list(root.get("suggestions"), "suggestions")
    if len(raw_suggestions) > 50:
        raise TeachingPrepValidationError("too many exercise suggestions")
    for raw in raw_suggestions:
        fields = {
            "material_version_id",
            "question_number",
            "content_label",
            "difficulty",
            "classroom_use",
            "estimated_minutes",
            "teaching_focus",
            "reason",
            "uncertainties",
            "question_regions",
            "answer_regions",
        }
        if set(raw) != fields:
            raise TeachingPrepValidationError(
                "exercise suggestion has missing or unsupported fields"
            )
        material_id = _id(raw.get("material_version_id"), "material version")
        if material_id not in materials:
            raise TeachingPrepValidationError(
                "exercise suggestion refers outside the reference snapshot"
            )
        question_regions = _regions(
            raw.get("question_regions"),
            allowed_units=allowed_units,
            material_version_id=material_id,
            label="question_regions",
        )
        if not question_regions:
            raise TeachingPrepValidationError(
                "exercise suggestion requires a question region"
            )
        result.append(
            {
                "material_version_id": material_id,
                "question_number": _optional_text(
                    raw.get("question_number"),
                    "question_number",
                    maximum=80,
                ),
                "content_label": _optional_text(
                    raw.get("content_label"), "content_label", maximum=500
                ),
                "difficulty": _choice(
                    _known_alias(raw.get("difficulty"), _DIFFICULTY_ALIASES),
                    _DIFFICULTIES,
                    "difficulty",
                ),
                "classroom_use": _choice(
                    _known_alias(
                        raw.get("classroom_use"), _CLASSROOM_USE_ALIASES
                    ),
                    _CLASSROOM_USES,
                    "classroom_use",
                ),
                "estimated_minutes": _optional_positive_int(
                    raw.get("estimated_minutes"), "estimated_minutes", maximum=90
                ),
                "teaching_focus": _optional_text(
                    raw.get("teaching_focus"), "teaching_focus", maximum=500
                ),
                "reason": _text(raw.get("reason"), "reason", maximum=1_000),
                "uncertainties": _strings(
                    _empty_string_as_list(raw.get("uncertainties")),
                    "uncertainties",
                    maximum=20,
                    item_maximum=500,
                ),
                "question_regions": question_regions,
                "answer_regions": _regions(
                    raw.get("answer_regions"),
                    allowed_units=allowed_units,
                    material_version_id=material_id,
                    label="answer_regions",
                ),
            }
        )
    return result


def _known_alias(value: object, aliases: Mapping[str, str]) -> object:
    if isinstance(value, str):
        return aliases.get(value.strip(), value)
    return value


def normalize_mapping_decision(
    value: Mapping[str, object],
) -> dict[str, object]:
    if set(value) - {
        "decision",
        "lesson_ref",
        "start_unit",
        "end_unit",
        "reason",
    }:
        raise TeachingPrepValidationError(
            "mapping decision has unsupported fields"
        )
    decision = _choice(
        value.get("decision"),
        {"accepted", "modified", "rejected"},
        "mapping decision",
    )
    result: dict[str, object] = {
        "decision": decision,
        "reason": _optional_text(value.get("reason"), "reason", maximum=500),
    }
    if decision in {"accepted", "modified"}:
        result.update(
            {
                "lesson_ref": _text(
                    value.get("lesson_ref"), "lesson_ref", maximum=128
                ),
                "start_unit": _positive_int(
                    value.get("start_unit"), "start_unit"
                ),
                "end_unit": _positive_int(value.get("end_unit"), "end_unit"),
            }
        )
        if int(result["end_unit"]) < int(result["start_unit"]):
            raise TeachingPrepValidationError("mapping range is invalid")
    return result


def _regions(
    value: object,
    *,
    allowed_units: Mapping[str, tuple[str, int]],
    material_version_id: str,
    label: str,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, raw in enumerate(_mapping_list(value, label), start=1):
        if set(raw) != {"material_unit_id", "sequence", "crop"}:
            raise TeachingPrepValidationError(f"{label} item is invalid")
        unit_id = _id(raw.get("material_unit_id"), "material unit")
        unit = allowed_units.get(unit_id)
        if unit is None or unit[0] != material_version_id:
            raise TeachingPrepValidationError(
                "exercise region refers outside the reference snapshot"
            )
        sequence = _positive_int(raw.get("sequence"), "sequence")
        if sequence != index:
            raise TeachingPrepValidationError(
                "exercise region sequence must be contiguous"
            )
        crop = _crop_mapping(raw.get("crop"))
        if set(crop) != {"x0", "y0", "x1", "y1"}:
            raise TeachingPrepValidationError("exercise crop is invalid")
        values = {name: _ratio(crop.get(name), name) for name in crop}
        if label == "answer_regions" and not any(values.values()):
            continue
        if values["x1"] <= values["x0"] or values["y1"] <= values["y0"]:
            raise TeachingPrepValidationError("exercise crop is empty")
        result.append(
            {
                "material_unit_id": unit_id,
                "sequence": sequence,
                "crop": values,
            }
        )
    if len(result) > 20:
        raise TeachingPrepValidationError("too many exercise regions")
    return result


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TeachingPrepValidationError(f"{label} must be an object")
    return value


def _crop_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, list) and len(value) == 4:
        return dict(zip(("x0", "y0", "x1", "y1"), value, strict=True))
    return _mapping(value, "crop")


def _empty_string_as_list(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return []
    return value


def _mapping_list(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise TeachingPrepValidationError(f"{label} must be a list")
    return [_mapping(item, label) for item in value]


def _text(value: object, label: str, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise TeachingPrepValidationError(f"{label} is invalid")
    return text


def _optional_text(value: object, label: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if len(text) != 32 or any(ch not in "0123456789abcdef" for ch in text):
        raise TeachingPrepValidationError(f"{label} ID is invalid")
    return text


def _ids(value: object, label: str, *, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TeachingPrepValidationError(f"{label} is invalid")
    return list(dict.fromkeys(_id(item, label) for item in value))


def _positive_ids(value: object, label: str, *, maximum: int) -> list[int]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TeachingPrepValidationError(f"{label} is invalid")
    result: list[int] = []
    for item in value:
        result.append(_positive_int(item, label))
    return list(dict.fromkeys(result))


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TeachingPrepValidationError(f"{label} is invalid")
    return value


def _optional_positive_int(
    value: object, label: str, *, maximum: int
) -> int | None:
    if value is None:
        return None
    result = _positive_int(value, label)
    if result > maximum:
        raise TeachingPrepValidationError(f"{label} is invalid")
    return result


def _ratio(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TeachingPrepValidationError(f"{label} is invalid")
    result = float(value)
    if result < 0 or result > 1:
        raise TeachingPrepValidationError(f"{label} is invalid")
    return result


def _strings(
    value: object,
    label: str,
    *,
    maximum: int,
    item_maximum: int,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise TeachingPrepValidationError(f"{label} is invalid")
    return list(
        dict.fromkeys(
            _text(item, label, maximum=item_maximum) for item in value
        )
    )


def _choice(value: object, choices: set[str], label: str) -> str:
    text = str(value or "").strip()
    if text not in choices:
        raise TeachingPrepValidationError(f"{label} is invalid")
    return text


__all__ = [
    "normalize_exercise_suggestion_payload",
    "normalize_mapping_decision",
    "normalize_reference_selection",
    "snapshot_model_payload",
]
