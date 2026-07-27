from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_CATALOG_PATH = Path(__file__).resolve().parent / "catalogs" / "bnu_math_2024.json"
_EXPECTED_VOLUME_IDS = (
    "bnu24-math-g7-upper",
    "bnu24-math-g7-lower",
    "bnu24-math-g8-upper",
    "bnu24-math-g8-lower",
)
_LEGACY_CHAPTER_LABELS = {
    "bnu24-math-g7-upper-c03": "七年级上册 第三章 字母表示数",
    "bnu24-math-g7-upper-c06": "七年级上册 第六章 丰富的数据世界",
    "bnu24-math-g7-lower-c03": "七年级下册 第六章 概率初步",
    "bnu24-math-g7-lower-c05": "七年级下册 第五章 生活中的轴对称",
    "bnu24-math-g7-lower-c06": "七年级下册 第三章 变量之间的关系",
    "bnu24-math-g8-upper-c07": "八年级上册 第七章 平行线的证明",
    "bnu24-math-g8-lower-c02": "八年级下册 第二章 一元一次不等式与一元一次不等式组",
}
_SECTION_KINDS = {
    "activity",
    "activity_group",
    "exercise",
    "lesson",
    "optional_lesson",
    "reflection",
    "review",
}


class CurriculumCatalogError(RuntimeError):
    """Raised when the bundled, read-only curriculum catalog is unavailable."""


def _required_string(container: dict[str, Any], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return value.strip()


def _nullable_string(container: dict[str, Any], key: str) -> str | None:
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return value.strip()


def _positive_integer(container: dict[str, Any], key: str) -> int:
    value = container.get(key)
    if type(value) is not int or value <= 0:  # noqa: E721 - bool is invalid here
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return value


def _load_json() -> dict[str, Any]:
    try:
        payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CurriculumCatalogError(
            "Bundled curriculum catalog is unavailable"
        ) from exc
    if not isinstance(payload, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return payload


def _validate_section(
    raw: Any,
    *,
    expected_order: int,
    seen_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    section_id = _required_string(raw, "id")
    if section_id in seen_ids:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids.add(section_id)
    order = _positive_integer(raw, "order")
    if order != expected_order:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    kind = _required_string(raw, "kind")
    if kind not in _SECTION_KINDS:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return {
        "id": section_id,
        "order": order,
        "number": _nullable_string(raw, "number"),
        "title": _required_string(raw, "title"),
        "label": _required_string(raw, "label"),
        "kind": kind,
    }


def _validate_chapter(
    raw: Any,
    *,
    volume_label: str,
    expected_order: int,
    seen_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    chapter_id = _required_string(raw, "id")
    if chapter_id in seen_ids:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids.add(chapter_id)
    order = _positive_integer(raw, "order")
    kind = _required_string(raw, "kind")
    if order != expected_order or kind not in {"chapter", "activity"}:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    label = _required_string(raw, "label")
    raw_sections = raw.get("sections")
    if not isinstance(raw_sections, list):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    sections = [
        _validate_section(section, expected_order=index, seen_ids=seen_ids)
        for index, section in enumerate(raw_sections, start=1)
    ]
    canonical_label = f"{volume_label} {label}"
    exam_scope_values = [canonical_label]
    legacy_label = _LEGACY_CHAPTER_LABELS.get(chapter_id)
    if legacy_label and legacy_label != canonical_label:
        exam_scope_values.append(legacy_label)
    return {
        "id": chapter_id,
        "order": order,
        "number": _nullable_string(raw, "number"),
        "title": _required_string(raw, "title"),
        "label": label,
        "kind": kind,
        "exam_scope_values": exam_scope_values,
        "sections": sections,
    }


def _validate_volume(
    raw: Any,
    *,
    expected_id: str,
    seen_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    volume_id = _required_string(raw, "id")
    if volume_id != expected_id or volume_id in seen_ids:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids.add(volume_id)
    volume_label = _required_string(raw, "label")
    raw_chapters = raw.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    chapters = [
        _validate_chapter(
            chapter,
            volume_label=volume_label,
            expected_order=index,
            seen_ids=seen_ids,
        )
        for index, chapter in enumerate(raw_chapters, start=1)
    ]
    return {
        "id": volume_id,
        "label": volume_label,
        "grade": _required_string(raw, "grade"),
        "semester": _required_string(raw, "semester"),
        "textbook_version": _required_string(raw, "textbook_version"),
        "chapters": chapters,
    }


@lru_cache(maxsize=1)
def load_curriculum_catalog() -> dict[str, Any]:
    """Load and validate the bundled catalog without any network access."""

    payload = _load_json()
    if payload.get("schema_version") != 1:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    capture = payload.get("capture")
    if not isinstance(capture, dict) or capture.get("runtime_refresh") is not False:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    raw_volumes = payload.get("volumes")
    if (
        not isinstance(raw_volumes, list)
        or len(raw_volumes) != len(_EXPECTED_VOLUME_IDS)
    ):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids: set[str] = set()
    volumes = [
        _validate_volume(volume, expected_id=expected_id, seen_ids=seen_ids)
        for volume, expected_id in zip(
            raw_volumes,
            _EXPECTED_VOLUME_IDS,
            strict=True,
        )
    ]
    return {
        "schema_version": 1,
        "catalog_id": _required_string(payload, "catalog_id"),
        "publisher": _required_string(payload, "publisher"),
        "subject": _required_string(payload, "subject"),
        "edition": _required_string(payload, "edition"),
        "volumes": volumes,
    }


def curriculum_chapter_exam_scope_values() -> dict[str, tuple[str, ...]]:
    return {
        chapter["id"]: tuple(chapter["exam_scope_values"])
        for volume in load_curriculum_catalog()["volumes"]
        for chapter in volume["chapters"]
    }
