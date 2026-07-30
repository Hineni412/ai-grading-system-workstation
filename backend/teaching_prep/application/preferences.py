from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from backend.teaching_prep.domain.errors import TeachingPrepValidationError


DEFAULT_TEACHING_PREFERENCES: dict[str, object] = {
    "schema_version": 1,
    "label_textbook_pages": True,
    "page_label_font_size": 28,
    "trim_excess_practice": True,
    "practice_trim_level": "moderate",
    "preserve_teaching_examples": True,
    "prefer_short_practice": True,
    "supplement_from_references": True,
    "supplement_question_limit": 2,
    "supplement_as_source_image": True,
    "prioritize_homework_workbook": True,
    "avoid_direct_homework_copy": True,
    "avoid_ppt_duplicates": True,
}

_BOOLEAN_KEYS = {
    "label_textbook_pages",
    "trim_excess_practice",
    "preserve_teaching_examples",
    "prefer_short_practice",
    "supplement_from_references",
    "supplement_as_source_image",
    "prioritize_homework_workbook",
    "avoid_direct_homework_copy",
    "avoid_ppt_duplicates",
}
_TRIM_LEVELS = {"light", "moderate", "strong"}


def normalize_teaching_preferences(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TeachingPrepValidationError(
            "teaching preferences must be an object"
        )
    item = dict(value)
    if set(item) != set(DEFAULT_TEACHING_PREFERENCES):
        raise TeachingPrepValidationError(
            "teaching preferences have missing or unsupported fields"
        )
    if item.get("schema_version") != 1:
        raise TeachingPrepValidationError(
            "teaching preferences schema is unsupported"
        )
    for key in _BOOLEAN_KEYS:
        if not isinstance(item.get(key), bool):
            raise TeachingPrepValidationError(
                f"teaching preference {key} must be boolean"
            )
    if item.get("page_label_font_size") != 28:
        raise TeachingPrepValidationError(
            "textbook page label font size must remain 28"
        )
    if item.get("practice_trim_level") not in _TRIM_LEVELS:
        raise TeachingPrepValidationError(
            "practice trim level is invalid"
        )
    limit = item.get("supplement_question_limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 3:
        raise TeachingPrepValidationError(
            "supplement question limit must be between zero and three"
        )
    if not item["supplement_from_references"] and limit != 0:
        raise TeachingPrepValidationError(
            "supplement question limit must be zero when supplementation is off"
        )
    if (
        not item["supplement_from_references"]
        and item["supplement_as_source_image"]
    ):
        raise TeachingPrepValidationError(
            "source-image supplementation requires supplementation to be on"
        )
    return {
        key: item[key]
        for key in DEFAULT_TEACHING_PREFERENCES
    }


def resolve_teaching_preferences(value: object) -> dict[str, object]:
    """Return frozen preferences, or legacy defaults without rewriting the pack."""
    if value is None:
        return deepcopy(DEFAULT_TEACHING_PREFERENCES)
    return normalize_teaching_preferences(value)


__all__ = [
    "DEFAULT_TEACHING_PREFERENCES",
    "normalize_teaching_preferences",
    "resolve_teaching_preferences",
]
