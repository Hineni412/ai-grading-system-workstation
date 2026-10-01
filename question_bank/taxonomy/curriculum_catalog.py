from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = (
    Path(__file__).resolve().parent / "catalogs" / "bnu_math_2024_v2.json"
)
_EXPECTED_VOLUME_IDS = (
    "bnu24-math-g7-upper",
    "bnu24-math-g7-lower",
    "bnu24-math-g8-upper",
    "bnu24-math-g8-lower",
    "bnu24-math-g9-upper",
)
_EXPECTED_RETAINED_COUNTS = (243, 185, 259, 229, 208)
_EXPECTED_TOTAL_NODES = 1124
_LEGACY_CHAPTER_LABELS = {
    "bnu24-math-g7-upper-c03": ("七年级上册 第三章 字母表示数",),
    "bnu24-math-g7-upper-c06": ("七年级上册 第六章 丰富的数据世界",),
    "bnu24-math-g7-lower-c03": ("七年级下册 第六章 概率初步",),
    "bnu24-math-g7-lower-c05": ("七年级下册 第五章 生活中的轴对称",),
    "bnu24-math-g7-lower-c06": ("七年级下册 第三章 变量之间的关系",),
    "bnu24-math-g8-upper-c07": ("八年级上册 第七章 平行线的证明",),
    "bnu24-math-g8-lower-c01": ("八年级下册 第一章 三角形的证明",),
    "bnu24-math-g8-lower-c02": (
        "八年级下册 第二章 一元一次不等式与一元一次不等式组",
    ),
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
    if type(value) is not int or value <= 0:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return value


def _non_negative_integer(container: dict[str, Any], key: str) -> int:
    value = container.get(key)
    if type(value) is not int or value < 0:
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


def _validate_source_ref(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return {
        "node_id": _required_string(raw, "node_id"),
        "relative_url": _required_string(raw, "relative_url"),
    }


def _validate_knowledge_point(
    raw: object,
    *,
    expected_order: int,
    expected_parent_id: str,
    seen_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    point_id = _required_string(raw, "id")
    if point_id in seen_ids:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids.add(point_id)
    order = _positive_integer(raw, "order")
    if order != expected_order:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    if _required_string(raw, "parent_knowledge_id") != expected_parent_id:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return {
        "id": point_id,
        "order": order,
        "label": _required_string(raw, "label"),
        "display_name": _required_string(raw, "display_name"),
        "parent_knowledge_id": expected_parent_id,
        "source_ref": _validate_source_ref(raw.get("source_ref")),
    }


def _validate_section(
    raw: object,
    *,
    expected_order: int,
    seen_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    section_id = _required_string(raw, "id")
    knowledge_id = _required_string(raw, "knowledge_id")
    if section_id in seen_ids or knowledge_id in seen_ids:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids.update((section_id, knowledge_id))
    order = _positive_integer(raw, "order")
    if order != expected_order:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    kind = _required_string(raw, "kind")
    if kind not in _SECTION_KINDS:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    raw_points = raw.get("knowledge_points")
    if not isinstance(raw_points, list):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    points = [
        _validate_knowledge_point(
            point,
            expected_order=index,
            expected_parent_id=knowledge_id,
            seen_ids=seen_ids,
        )
        for index, point in enumerate(raw_points, start=1)
    ]
    return {
        "id": section_id,
        "knowledge_id": knowledge_id,
        "order": order,
        "number": _nullable_string(raw, "number"),
        "title": _required_string(raw, "title"),
        "label": _required_string(raw, "label"),
        "kind": kind,
        "display_name": _required_string(raw, "display_name"),
        "source_ref": _validate_source_ref(raw.get("source_ref")),
        "knowledge_points": points,
    }


def _validate_chapter(
    raw: object,
    *,
    volume_label: str,
    expected_order: int,
    seen_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    chapter_id = _required_string(raw, "id")
    knowledge_id = _required_string(raw, "knowledge_id")
    if chapter_id in seen_ids or knowledge_id in seen_ids:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids.update((chapter_id, knowledge_id))
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
    for legacy_label in _LEGACY_CHAPTER_LABELS.get(chapter_id, ()):
        if legacy_label not in exam_scope_values:
            exam_scope_values.append(legacy_label)
    return {
        "id": chapter_id,
        "knowledge_id": knowledge_id,
        "order": order,
        "number": _nullable_string(raw, "number"),
        "title": _required_string(raw, "title"),
        "label": label,
        "kind": kind,
        "display_name": _required_string(raw, "display_name"),
        "source_ref": _validate_source_ref(raw.get("source_ref")),
        "exam_scope_values": exam_scope_values,
        "sections": sections,
    }


def _validate_volume(
    raw: object,
    *,
    expected_id: str,
    expected_order: int,
    expected_node_count: int,
    seen_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    volume_id = _required_string(raw, "id")
    if volume_id != expected_id or volume_id in seen_ids:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids.add(volume_id)
    if _positive_integer(raw, "order") != expected_order:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
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
    statistics = raw.get("statistics")
    if not isinstance(statistics, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    if _non_negative_integer(statistics, "retained_nodes") != expected_node_count:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    source = raw.get("source")
    if not isinstance(source, dict):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return {
        "id": volume_id,
        "order": expected_order,
        "label": volume_label,
        "grade": _required_string(raw, "grade"),
        "semester": _required_string(raw, "semester"),
        "textbook_version": _required_string(raw, "textbook_version"),
        "source": dict(source),
        "statistics": {
            "raw_nodes": _non_negative_integer(statistics, "raw_nodes"),
            "excluded_nodes": _non_negative_integer(statistics, "excluded_nodes"),
            "retained_nodes": expected_node_count,
        },
        "chapters": chapters,
    }


@lru_cache(maxsize=1)
def load_curriculum_catalog() -> dict[str, Any]:
    """Load and validate the bundled five-volume catalog without network I/O."""

    payload = _load_json()
    if payload.get("schema_version") != 2:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    capture = payload.get("capture")
    statistics = payload.get("statistics")
    if (
        not isinstance(capture, dict)
        or capture.get("runtime_refresh") is not False
        or not isinstance(statistics, dict)
        or _non_negative_integer(statistics, "retained_nodes")
        != _EXPECTED_TOTAL_NODES
    ):
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    raw_volumes = payload.get("volumes")
    if not isinstance(raw_volumes, list) or len(raw_volumes) != 5:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    seen_ids: set[str] = set()
    volumes = [
        _validate_volume(
            volume,
            expected_id=expected_id,
            expected_order=index,
            expected_node_count=_EXPECTED_RETAINED_COUNTS[index - 1],
            seen_ids=seen_ids,
        )
        for index, (volume, expected_id) in enumerate(
            zip(raw_volumes, _EXPECTED_VOLUME_IDS, strict=True),
            start=1,
        )
    ]
    knowledge_count = sum(
        1 + len(chapter["sections"]) + sum(
            len(section["knowledge_points"])
            for section in chapter["sections"]
        )
        for volume in volumes
        for chapter in volume["chapters"]
    )
    if knowledge_count != _EXPECTED_TOTAL_NODES:
        raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
    return {
        "schema_version": 2,
        "catalog_id": _required_string(payload, "catalog_id"),
        "knowledge_standard_id": _required_string(
            payload, "knowledge_standard_id"
        ),
        "publisher": _required_string(payload, "publisher"),
        "subject": _required_string(payload, "subject"),
        "edition": _required_string(payload, "edition"),
        "statistics": {
            "raw_nodes": _non_negative_integer(statistics, "raw_nodes"),
            "excluded_nodes": _non_negative_integer(statistics, "excluded_nodes"),
            "retained_nodes": _EXPECTED_TOTAL_NODES,
            "chapters": _non_negative_integer(statistics, "chapters"),
            "sections": _non_negative_integer(statistics, "sections"),
            "knowledge_points": _non_negative_integer(
                statistics, "knowledge_points"
            ),
        },
        "volumes": volumes,
    }


def curriculum_chapter_exam_scope_values() -> dict[str, tuple[str, ...]]:
    return {
        chapter["id"]: tuple(chapter["exam_scope_values"])
        for volume in load_curriculum_catalog()["volumes"]
        for chapter in volume["chapters"]
    }


def curriculum_volume(
    *,
    volume_id: object = "",
    grade: object = "",
    semester: object = "",
    textbook_version: object = "",
) -> dict[str, Any] | None:
    """Resolve one bundled volume from stable ID or exact paper metadata."""

    clean_id = str(volume_id or "").strip()
    clean_grade = str(grade or "").strip()
    clean_semester = str(semester or "").strip()
    clean_version = str(textbook_version or "").strip()
    for volume in load_curriculum_catalog()["volumes"]:
        if clean_id:
            if volume["id"] == clean_id:
                return volume
            continue
        if (
            clean_grade
            and clean_semester
            and volume["grade"] == clean_grade
            and volume["semester"] == clean_semester
            and (not clean_version or volume["textbook_version"] == clean_version)
        ):
            return volume
    return None


@lru_cache(maxsize=1)
def _knowledge_nodes() -> tuple[dict[str, Any], ...]:
    nodes: list[dict[str, Any]] = []
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume["chapters"]:
            chapter_node = {
                "id": chapter["knowledge_id"],
                "name": chapter["display_name"],
                "label": chapter["label"],
                "level": 1,
                "parent_id": None,
                "volume_id": volume["id"],
                "volume_order": volume["order"],
                "source_ref": chapter["source_ref"],
            }
            nodes.append(chapter_node)
            for section in chapter["sections"]:
                section_node = {
                    "id": section["knowledge_id"],
                    "name": section["display_name"],
                    "label": section["label"],
                    "level": 2,
                    "parent_id": chapter["knowledge_id"],
                    "volume_id": volume["id"],
                    "volume_order": volume["order"],
                    "source_ref": section["source_ref"],
                }
                nodes.append(section_node)
                nodes.extend(
                    {
                        "id": point["id"],
                        "name": point["display_name"],
                        "label": point["label"],
                        "level": 3,
                        "parent_id": section["knowledge_id"],
                        "volume_id": volume["id"],
                        "volume_order": volume["order"],
                        "source_ref": point["source_ref"],
                    }
                    for point in section["knowledge_points"]
                )
    return tuple(nodes)


@lru_cache(maxsize=1)
def _knowledge_node_index() -> dict[str, dict[str, Any]]:
    return {str(node["id"]): node for node in _knowledge_nodes()}


def eligible_curriculum_knowledge_nodes(
    volume_id: object,
) -> tuple[dict[str, Any], ...]:
    """Return nodes from the selected volume and every earlier bundled volume."""

    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        return ()
    selected_order = int(volume["order"])
    return tuple(
        dict(node)
        for node in _knowledge_nodes()
        if int(node["volume_order"]) <= selected_order
    )


def curriculum_knowledge_ancestors(knowledge_id: object) -> tuple[str, ...]:
    """Return the nearest parent first, followed by the remaining parent chain."""

    index = _knowledge_node_index()
    current = index.get(str(knowledge_id or "").strip())
    result: list[str] = []
    while current is not None and current.get("parent_id"):
        parent_id = str(current["parent_id"])
        if parent_id in result:
            raise CurriculumCatalogError("Bundled curriculum catalog is invalid")
        result.append(parent_id)
        current = index.get(parent_id)
    return tuple(result)


def curriculum_knowledge_node(knowledge_id: object) -> dict[str, Any] | None:
    node = _knowledge_node_index().get(str(knowledge_id or "").strip())
    return None if node is None else dict(node)


def curriculum_volume_contract(volume_id: object) -> dict[str, Any] | None:
    """Return the scoped curriculum and cumulative knowledge selection contract."""

    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        return None
    chapters = []
    sections = []
    for chapter in volume["chapters"]:
        chapter_name = str(chapter["exam_scope_values"][0])
        chapters.append(
            {
                "id": chapter["id"],
                "name": chapter_name,
                "knowledge_id": chapter["knowledge_id"],
            }
        )
        sections.extend(
            {
                "id": section["id"],
                "name": section["label"],
                "chapter_id": chapter["id"],
                "chapter_name": chapter_name,
                "knowledge_id": section["knowledge_id"],
            }
            for section in chapter["sections"]
        )
    eligible_nodes = eligible_curriculum_knowledge_nodes(volume["id"])
    eligible_volume_ids = [
        item["id"]
        for item in load_curriculum_catalog()["volumes"]
        if int(item["order"]) <= int(volume["order"])
    ]
    return {
        "id": volume["id"],
        "label": volume["label"],
        "grade": volume["grade"],
        "semester": volume["semester"],
        "textbook_version": volume["textbook_version"],
        "knowledge_standard_id": load_curriculum_catalog()[
            "knowledge_standard_id"
        ],
        "eligible_knowledge_volume_ids": eligible_volume_ids,
        "eligible_knowledge_count": len(eligible_nodes),
        "chapters": chapters,
        "sections": sections,
    }


def teaching_progress_allowed_prefixes(chapter_id: object) -> tuple[str, ...] | None:
    """返回该章作为教学进度上限时，已学范围允许的知识点路径前缀。

    已学范围 = 目录中册序更小的所有册（整册）+ 本册章序不超过该章的所有章。
    章节 ID 无法解析时返回 None（调用方失败关闭，不加过滤）。"""

    clean_id = str(chapter_id or "").strip()
    if not clean_id:
        return None
    for volume in load_curriculum_catalog()["volumes"]:
        chapter = next(
            (item for item in volume["chapters"] if item["id"] == clean_id),
            None,
        )
        if chapter is None:
            continue
        volume_order = int(volume["order"])
        chapter_order = int(chapter["order"])
        prefixes = [
            f"{item['label']}｜"
            for item in load_curriculum_catalog()["volumes"]
            if int(item["order"]) < volume_order
        ]
        prefixes.extend(
            f"{volume['label']}｜{item['label']}｜"
            for item in volume["chapters"]
            if int(item["order"]) <= chapter_order
        )
        return tuple(prefixes)
    return None


def _volume_key_stem(volume: Mapping[str, Any]) -> str:
    """册的稳定键主干（如 bnu24_math_g8_upper），由第一章 knowledge_id 派生。"""

    for chapter in volume["chapters"]:
        match = re.match(
            r"^kp_([A-Za-z0-9]+_[A-Za-z0-9]+_g\d+_(?:upper|lower))_\d+$",
            str(chapter.get("knowledge_id") or ""),
        )
        if match:
            return match.group(1)
    return ""


def teaching_progress_allowed_stable_keys(
    chapter_id: object,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """返回已学范围允许的稳定键：(精确匹配集合, LIKE 前缀集合)。

    与 teaching_progress_allowed_prefixes 同一口径，但针对新口径写入的
    稳定键标签值（kp_…/sk_…）：册序更小的册整册允许，本册章序不超过
    该章的章内全部节点键允许。章节 ID 无法解析时返回 None。
    """

    clean_id = str(chapter_id or "").strip()
    if not clean_id:
        return None
    for volume in load_curriculum_catalog()["volumes"]:
        chapter = next(
            (item for item in volume["chapters"] if item["id"] == clean_id),
            None,
        )
        if chapter is None:
            continue
        volume_order = int(volume["order"])
        chapter_order = int(chapter["order"])
        exact: list[str] = []
        prefixes: list[str] = []
        for item in load_curriculum_catalog()["volumes"]:
            if int(item["order"]) >= volume_order:
                continue
            stem = _volume_key_stem(item)
            if not stem:
                continue
            # 更早册别整册允许：覆盖该册全部 kp_/sk_ 稳定键。
            exact.append(f"kp_{stem}")
            prefixes.append(f"kp_{stem}_")
            prefixes.append(f"sk_{stem}_")
        stem = _volume_key_stem(volume)
        for item in volume["chapters"]:
            if int(item["order"]) > chapter_order:
                # 综合与实践（activity）章下的技能是跨章节考法技能（如运用题设
                # 新定义），不随章序解锁；该章自身的活动小节仍按章序。
                if stem and str(item.get("kind") or "") == "activity":
                    chapter_number = str(item["knowledge_id"]).rsplit("_", 1)[-1]
                    prefixes.append(f"sk_{stem}_{chapter_number}_")
                continue
            knowledge_id = str(item["knowledge_id"])
            exact.append(knowledge_id)
            # 章内小节/词条/技能键都以“章号_”为下一段。
            prefixes.append(f"{knowledge_id}_")
            if stem:
                chapter_number = knowledge_id.rsplit("_", 1)[-1]
                prefixes.append(f"sk_{stem}_{chapter_number}_")
        return tuple(dict.fromkeys(exact)), tuple(dict.fromkeys(prefixes))
    return None


def teaching_progress_allowed_exam_scope_values(
    chapter_id: object,
) -> tuple[str, ...] | None:
    """返回该章作为教学进度上限时，已学范围允许的 exam_scope 值集合。

    已学范围 = 目录中册序更小的所有册（整册）+ 本册章序不超过该章的所有章；
    返回这些章的 exam_scope_values 的并集。
    章节 ID 无法解析时返回 None（调用方失败关闭，不加过滤）。"""

    clean_id = str(chapter_id or "").strip()
    if not clean_id:
        return None
    for volume in load_curriculum_catalog()["volumes"]:
        chapter = next(
            (item for item in volume["chapters"] if item["id"] == clean_id),
            None,
        )
        if chapter is None:
            continue
        volume_order = int(volume["order"])
        chapter_order = int(chapter["order"])
        values: list[str] = []
        for item in load_curriculum_catalog()["volumes"]:
            if int(item["order"]) >= volume_order:
                continue
            for earlier_chapter in item["chapters"]:
                values.extend(earlier_chapter["exam_scope_values"])
        values.extend(
            scope_value
            for item in volume["chapters"]
            if int(item["order"]) <= chapter_order
            for scope_value in item["exam_scope_values"]
        )
        return tuple(dict.fromkeys(values))
    return None


def infer_curriculum_volume_from_text(value: object) -> dict[str, Any] | None:
    """Infer only when one grade and one semester marker are unambiguous."""

    text = re.sub(r"\s+", "", str(value or ""))
    grades = set(re.findall(r"([七八九])年级", text))
    semesters = {
        marker
        for marker, patterns in (
            ("上", ("上册", "上学期")),
            ("下", ("下册", "下学期")),
        )
        if any(pattern in text for pattern in patterns)
    }
    if len(grades) != 1 or len(semesters) != 1:
        return None
    grade = f"{next(iter(grades))}年级"
    semester = next(iter(semesters))
    for volume in load_curriculum_catalog()["volumes"]:
        if volume["grade"] == grade and (
            semester in volume["semester"] or semester in volume["label"]
        ):
            return volume
    return None


__all__ = [
    "CurriculumCatalogError",
    "curriculum_chapter_exam_scope_values",
    "curriculum_knowledge_ancestors",
    "curriculum_knowledge_node",
    "curriculum_volume",
    "curriculum_volume_contract",
    "eligible_curriculum_knowledge_nodes",
    "infer_curriculum_volume_from_text",
    "load_curriculum_catalog",
    "teaching_progress_allowed_exam_scope_values",
    "teaching_progress_allowed_prefixes",
]
