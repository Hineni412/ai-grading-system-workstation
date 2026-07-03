from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


MIN_REGION_SIZE = 12
EDGE_SNAP_TOLERANCE = 8
MappingStatus = Literal["auto", "manual", "unbound"]

_STUDENT_NAME_REGION_ID = "__student_name__"
_VALID_MAPPING_STATUSES = {"auto", "manual", "unbound"}
_PAGE_ORDER = {"front": 0, "back": 1}


@dataclass(frozen=True)
class RegionIssue:
    code: str
    message: str
    region_uuid: str | None = None
    question_id: str | None = None


@dataclass(frozen=True)
class RegionValidationResult:
    issues: tuple[RegionIssue, ...]

    @property
    def can_commit(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class QuestionBindingOption:
    value: str
    label: str


@dataclass(frozen=True)
class QuestionBindingCatalog:
    automatic_candidates: tuple[str, ...]
    manual_options: tuple[QuestionBindingOption, ...]
    parent_question_ids: frozenset[str]


def normalize_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[tuple[int, dict[str, Any]]] = []
    for position, source in enumerate(regions):
        region = dict(source)
        raw_uuid = region.get("region_uuid")
        region["region_uuid"] = (
            str(raw_uuid) if raw_uuid is not None and str(raw_uuid).strip() else str(uuid4())
        )
        region["page"] = "back" if str(region.get("page") or "").strip() == "back" else "front"
        for key in ("x", "y", "w", "h"):
            region[key] = _integer_coordinate(region.get(key))

        mapped_question_id = _optional_text(region.get("mapped_question_id"))
        region["mapped_question_id"] = mapped_question_id
        raw_status = str(region.get("mapping_status") or "").strip()
        if mapped_question_id is None:
            region["mapping_status"] = "unbound"
        elif raw_status in _VALID_MAPPING_STATUSES - {"unbound"}:
            region["mapping_status"] = raw_status
        else:
            region["mapping_status"] = "manual"

        region["is_confirmed"] = _boolean_flag(region.get("is_confirmed", False))
        region["multi_region_confirmed"] = _boolean_flag(region.get("multi_region_confirmed", False))
        normalized.append((position, region))

    normalized.sort(
        key=lambda item: (
            _PAGE_ORDER[item[1]["page"]],
            _integer_coordinate(item[1].get("region_order")),
            item[0],
        )
    )
    result = [region for _, region in normalized]
    for order, region in enumerate(result, start=1):
        region["region_order"] = order
    return result


def assign_sequential_question_ids(
    regions: list[dict[str, Any]],
    question_candidates: list[str],
) -> list[dict[str, Any]]:
    result = [dict(region) for region in regions]
    used = {
        question_id
        for region in result
        if (question_id := _optional_text(region.get("mapped_question_id"))) is not None
    }
    candidates = _unique_nonblank(question_candidates, exclude={_STUDENT_NAME_REGION_ID})

    for region in result:
        if _optional_text(region.get("mapped_question_id")) is not None:
            continue
        next_candidate = next((candidate for candidate in candidates if candidate not in used), None)
        if next_candidate is None:
            region["mapped_question_id"] = None
            region["mapping_status"] = "unbound"
            continue
        region["mapped_question_id"] = next_candidate
        region["mapping_status"] = "auto"
        used.add(next_candidate)
    return result


def load_question_binding_catalog(rubric_path: Path) -> QuestionBindingCatalog:
    rubric: dict[str, Any] = {}
    try:
        parsed = json.loads(rubric_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            rubric = parsed
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        rubric = {}

    # 通过统一题号契约得到规范明细号；单小问大题折叠为父题号，多小问展开为 Q<n>(P<m>)。
    from question_id_contract import QuestionIdCatalog

    catalog = None
    try:
        catalog = QuestionIdCatalog.from_document(rubric)
    except Exception:
        catalog = None

    if catalog is not None:
        automatic_candidates = list(catalog.detail_ids)
        # 只有含多个小问的大题才作为"整道大题"手动绑定父题。
        parents = [
            parent
            for parent in catalog.parent_ids
            if len(catalog.parts_by_parent.get(parent, ())) > 1
        ]
    else:
        # 契约无法解析时退回原始逐题收集，保证异常题号不导致空目录。
        questions = [
            question
            for question in (rubric.get("questions") if isinstance(rubric.get("questions"), list) else [])
            if isinstance(question, dict)
        ]
        automatic_candidates = []
        parents = []
        for question in questions:
            question_id = _optional_text(question.get("question_id"))
            part_ids = [
                part_id
                for part in question.get("parts", [])
                if isinstance(part, dict)
                and (part_id := _optional_text(part.get("part_id"))) is not None
            ] if isinstance(question.get("parts"), list) else []
            if question_id and question_id != _STUDENT_NAME_REGION_ID and len(part_ids) > 1:
                parents.append(question_id)
            automatic_candidates.extend(part_ids if part_ids else ([question_id] if question_id else []))

    automatic = _unique_nonblank(automatic_candidates, exclude={_STUDENT_NAME_REGION_ID})
    parent_ids = _unique_nonblank(parents, exclude={_STUDENT_NAME_REGION_ID})
    parent_id_set = set(parent_ids)
    manual_values = _manual_binding_values(automatic, parent_ids)
    manual_options = tuple(
        QuestionBindingOption(value=value, label=_binding_label(value, parent_id_set))
        for value in manual_values
    )
    return QuestionBindingCatalog(
        automatic_candidates=tuple(automatic),
        manual_options=manual_options,
        parent_question_ids=frozenset(parent_ids),
    )


def validate_regions(
    regions: list[dict[str, Any]],
    *,
    image_sizes: dict[str, tuple[int, int]],
    template_matches: bool,
) -> RegionValidationResult:
    issues: list[RegionIssue] = []
    if not template_matches:
        issues.append(RegionIssue("template_mismatch", "Draft regions do not match the current template."))

    seen_uuids: set[str] = set()
    duplicate_uuids: set[str] = set()
    question_groups: dict[str, list[dict[str, Any]]] = {}

    for region in regions:
        region_uuid = _optional_text(region.get("region_uuid"))
        if region_uuid in seen_uuids and region_uuid not in duplicate_uuids:
            issues.append(
                RegionIssue(
                    "duplicate_uuid",
                    f"Region UUID {region_uuid} is duplicated.",
                    region_uuid=region_uuid,
                )
            )
            duplicate_uuids.add(region_uuid)
        elif region_uuid is not None:
            seen_uuids.add(region_uuid)

        question_id = _optional_text(region.get("mapped_question_id"))
        if question_id is None:
            issues.append(
                RegionIssue(
                    "unbound_question",
                    "Region is not bound to a question.",
                    region_uuid=region_uuid,
                )
            )
        else:
            question_groups.setdefault(question_id, []).append(region)

        geometry, geometry_is_valid = _region_geometry(region)
        if not geometry_is_valid:
            issues.append(
                RegionIssue(
                    "region_out_of_bounds",
                    "Region geometry contains invalid coordinates.",
                    region_uuid=region_uuid,
                    question_id=question_id,
                )
            )
        elif template_matches:
            page = str(region.get("page") or "")
            image_size = image_sizes.get(page)
            if not _valid_image_size(image_size):
                issues.append(
                    RegionIssue(
                        "region_out_of_bounds",
                        f"Region page {page!r} has no valid image bounds.",
                        region_uuid=region_uuid,
                        question_id=question_id,
                    )
                )
            elif not _clamp_or_reject_region(region, image_size, geometry):
                issues.append(
                    RegionIssue(
                        "region_out_of_bounds",
                        "Region is clearly outside the image bounds.",
                        region_uuid=region_uuid,
                        question_id=question_id,
                    )
                )

        if geometry["w"] < MIN_REGION_SIZE or geometry["h"] < MIN_REGION_SIZE:
            issues.append(
                RegionIssue(
                    "region_too_small",
                    f"Region must be at least {MIN_REGION_SIZE} by {MIN_REGION_SIZE} pixels.",
                    region_uuid=region_uuid,
                    question_id=question_id,
                )
            )

    for question_id, group in question_groups.items():
        if len(group) > 1 and not all(bool(region.get("multi_region_confirmed")) for region in group):
            issues.append(
                RegionIssue(
                    "unconfirmed_multi_region",
                    f"Multiple regions for {question_id} have not all been confirmed.",
                    question_id=question_id,
                )
            )

    return RegionValidationResult(tuple(issues))


def _integer_coordinate(value: Any) -> int:
    return _coordinate_value(value)[0]


def _coordinate_value(value: Any) -> tuple[int, bool]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0, False
    if not math.isfinite(number):
        return 0, False
    return int(round(number)), True


def _region_geometry(region: dict[str, Any]) -> tuple[dict[str, int], bool]:
    geometry: dict[str, int] = {}
    is_valid = True
    for key in ("x", "y", "w", "h"):
        geometry[key], coordinate_is_valid = _coordinate_value(region.get(key))
        is_valid = is_valid and coordinate_is_valid
    return geometry, is_valid


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _boolean_flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _unique_nonblank(values: list[Any], *, exclude: set[str] | None = None) -> list[str]:
    excluded = exclude or set()
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _optional_text(value)
        if text is None or text in excluded or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _manual_binding_values(automatic_candidates: list[str], parent_ids: list[str]) -> list[str]:
    body: list[str] = []
    emitted_parents: set[str] = set()
    for candidate in automatic_candidates:
        parent = next(
            (
                parent_id
                for parent_id in parent_ids
                if candidate == parent_id
                or any(
                    candidate.startswith(parent_id + separator)
                    for separator in ("(", "（", "-", "_", ".")
                )
            ),
            None,
        )
        if parent is not None and parent not in emitted_parents:
            body.append(parent)
            emitted_parents.add(parent)
        if candidate not in body:
            body.append(candidate)
    body.extend(parent_id for parent_id in parent_ids if parent_id not in body)
    return ["", _STUDENT_NAME_REGION_ID, *body]


def _binding_label(value: str, parent_ids: set[str]) -> str:
    if value == _STUDENT_NAME_REGION_ID:
        return "姓名识别区域"
    if value in parent_ids:
        return f"{value}（整道大题）"
    return value


def _valid_image_size(image_size: object) -> bool:
    if not isinstance(image_size, tuple) or len(image_size) != 2:
        return False
    return all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in image_size
    )


def _clamp_or_reject_region(
    region: dict[str, Any],
    image_size: tuple[int, int],
    geometry: dict[str, int],
) -> bool:
    image_width, image_height = image_size
    x = geometry["x"]
    y = geometry["y"]
    w = geometry["w"]
    h = geometry["h"]
    right = x + w
    bottom = y + h

    if right <= 0 or bottom <= 0 or x >= image_width or y >= image_height:
        return False

    overflow = (-x, -y, right - image_width, bottom - image_height)
    if any(amount > EDGE_SNAP_TOLERANCE for amount in overflow):
        return False

    left = max(0, x)
    top = max(0, y)
    clamped_right = min(image_width, right)
    clamped_bottom = min(image_height, bottom)
    region.update(
        {
            "x": left,
            "y": top,
            "w": clamped_right - left,
            "h": clamped_bottom - top,
        }
    )
    return True
