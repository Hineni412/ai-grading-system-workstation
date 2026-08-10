from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path

from PIL import Image

from backend.teaching_prep.application.slide_plans import (
    diff_preview,
    require_approval_allowed,
    validate_plan_payload,
)
from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.infrastructure.materials import MaterialParser


def build_executor_request(
    plan_payload: Mapping[str, object],
    *,
    source_sha256: str,
    source_copy: Path,
    candidate: Path,
    preview_dir: Path,
    resolve_asset: Callable[[str, str], Path],
) -> dict[str, object]:
    payload = validate_plan_payload(plan_payload)
    operations: list[dict[str, object]] = []
    for raw in payload["operations"]:
        operation = dict(raw)
        decision = str(operation["decision"])
        if decision == "proposed":
            raise TeachingPrepValidationError(
                "every slide operation must be decided before execution"
            )
        if decision != "approved":
            continue
        require_approval_allowed(operation)
        if operation["execution_mode"] != "automatic":
            continue
        target = dict(operation["target"])
        executor_target = {
            key: target.get(key)
            for key in (
                "target_kind",
                "slide_signature",
                "generated_page_number",
                "new_slide_operation_id",
                "wps_object_id",
                "object_type",
                "position",
                "match_strategy",
            )
        }
        details = dict(operation["details"])
        executor_details = {
            key: details.get(key)
            for key in (
                "insert_after_signature",
                "layout_source_signature",
                "has_object_animation",
                "destination_index",
                "target_position",
                "text",
                "font_size",
                "semantic_role",
                "crop_left",
                "crop_top",
                "crop_right",
                "crop_bottom",
            )
            if key in details
        }
        asset_ref = details.get("asset_ref")
        if asset_ref is not None:
            isolated_asset = resolve_asset(
                str(asset_ref),
                str(operation["operation_id"]),
            )
            executor_details["isolated_asset_path"] = str(isolated_asset)
            executor_details["asset_source_sha256"] = details.get(
                "asset_source_sha256"
            )
        operations.append(
            {
                "operation_id": str(operation["operation_id"]),
                "kind": str(operation["kind"]),
                "target": executor_target,
                "details": executor_details,
            }
        )
    return {
        "schema_version": 1,
        "source": {
            "isolated_copy_path": str(source_copy),
            "expected_sha256": source_sha256,
        },
        "output": {
            "candidate_path": str(candidate),
            "preview_directory": str(preview_dir),
        },
        "operations": operations,
        "verification_contract": {
            "must_reopen": True,
            "must_render_every_slide": True,
            "must_run_slideshow_check": True,
            "must_preserve_unapproved_content": True,
            "must_fail_closed": True,
        },
    }


def verify_candidate(
    *,
    candidate: Path,
    preview_dir: Path,
    plan_payload: Mapping[str, object],
    expected_slide_count: int,
    source_path: Path,
    source_sha256: str,
    execution_report: Mapping[str, object],
    parser: MaterialParser,
    require_budget: Callable[[], None] | None = None,
) -> dict[str, object]:
    check_budget = require_budget or (lambda: None)
    check_budget()
    if not candidate.is_file():
        raise TeachingPrepValidationError(
            "WPS execution did not create a candidate PPTX"
        )
    if _sha256(source_path) != source_sha256:
        raise TeachingPrepValidationError(
            "source PPTX changed during execution"
        )
    report = _validated_execution_report(execution_report)
    payload = validate_plan_payload(plan_payload)
    approved_ids = sorted(
        str(item["operation_id"])
        for item in payload["operations"]
        if item["decision"] == "approved"
        and item["execution_mode"] == "automatic"
    )
    if sorted(report["applied_operation_ids"]) != approved_ids:
        raise TeachingPrepValidationError(
            "WPS applied operation list does not match the approved plan"
        )
    check_budget()
    source_units = parser.parse(source_path, material_type="pptx")
    units = parser.parse(candidate, material_type="pptx")
    check_budget()
    actual_slide_count = len(units)
    if actual_slide_count != expected_slide_count:
        raise TeachingPrepValidationError(
            "candidate PPTX page count does not match the approved plan"
        )
    readability = _readability(candidate)
    if not all(readability.values()):
        raise TeachingPrepValidationError(
            "candidate PPTX theme, master, or page size is unreadable"
        )
    preview_files = sorted(preview_dir.glob("slide-*.png"))
    check_budget()
    if len(preview_files) != actual_slide_count:
        raise TeachingPrepValidationError(
            "WPS did not render a complete preview set"
        )
    for preview in preview_files:
        check_budget()
        try:
            with Image.open(preview) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise TeachingPrepValidationError(
                "WPS preview output is invalid"
            ) from exc
    check_budget()
    _require_surviving_titles(payload, units)
    check_budget()
    expected_protected = _expected_protected_counts(
        payload,
        source_units=source_units,
    )
    actual_protected = _actual_protected_counts(units)
    if actual_protected != expected_protected:
        raise TeachingPrepValidationError(
            "protected PPTX objects changed unexpectedly"
        )
    check_budget()
    candidate_sha256 = _sha256(candidate)
    check_budget()
    return {
        "schema_version": 1,
        "verified": True,
        "candidate_sha256": candidate_sha256,
        "source_sha256_unchanged": True,
        "expected_slide_count": expected_slide_count,
        "actual_slide_count": actual_slide_count,
        "approved_operation_count": len(approved_ids),
        "approved_operations_applied": True,
        "unapproved_content_preserved": True,
        "protected_objects_preserved": True,
        "theme_readable": readability["theme_readable"],
        "master_readable": readability["master_readable"],
        "page_size_readable": readability["page_size_readable"],
        "reopened_in_wps": True,
        "rendered_preview_count": len(preview_files),
        "slideshow_check_passed": True,
        "mathematical_correctness_checked": False,
        "mathematical_correctness_note": (
            "只验证文件结构、批准操作和可播放性；数学内容仍需教师确认。"
        ),
    }


def safe_execution_report(value: Mapping[str, object]) -> dict[str, object]:
    report = _validated_execution_report(value)
    return {
        "schema_version": 1,
        "status": "completed",
        "source_lock_check": "passed",
        "applied_operation_ids": list(report["applied_operation_ids"]),
        "reopened_in_wps": True,
        "rendered_all_slides": True,
        "slideshow_check_passed": True,
        "unapproved_content_preserved": True,
    }


def _validated_execution_report(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TeachingPrepValidationError(
            "WPS execution report is invalid"
        )
    required_true = (
        "reopened_in_wps",
        "rendered_all_slides",
        "slideshow_check_passed",
        "unapproved_content_preserved",
    )
    if value.get("status") != "completed":
        raise TeachingPrepValidationError("WPS execution did not complete")
    if value.get("source_lock_check") != "passed":
        raise TeachingPrepValidationError(
            "source PPTX lock check did not pass"
        )
    if any(value.get(key) is not True for key in required_true):
        raise TeachingPrepValidationError(
            "WPS execution verification contract was not satisfied"
        )
    applied = value.get("applied_operation_ids")
    if (
        not isinstance(applied, list)
        or len(applied) > 1_000
        or any(not isinstance(item, str) for item in applied)
        or len(applied) != len(set(applied))
    ):
        raise TeachingPrepValidationError(
            "WPS applied operation report is invalid"
        )
    return {
        "applied_operation_ids": list(applied),
        **{key: True for key in required_true},
    }


def _readability(path: Path) -> dict[str, bool]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            presentation = archive.read("ppt/presentation.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return {
            "theme_readable": False,
            "master_readable": False,
            "page_size_readable": False,
        }
    page_size = b"<p:sldSz" in presentation or b":sldSz" in presentation
    return {
        "theme_readable": any(
            name.startswith("ppt/theme/theme") and name.endswith(".xml")
            for name in names
        ),
        "master_readable": any(
            name.startswith("ppt/slideMasters/slideMaster")
            and name.endswith(".xml")
            for name in names
        ),
        "page_size_readable": page_size,
    }


def _require_surviving_titles(
    payload: Mapping[str, object],
    units: tuple[object, ...],
) -> None:
    source_titles = {
        str(item.get("stable_signature") or ""): str(
            item.get("title") or ""
        ).strip()
        for item in payload["slides"]
    }
    title_may_change = {
        str(dict(item["target"]).get("slide_signature"))
        for item in payload["operations"]
        if item["decision"] == "approved"
        and item["kind"] == "delete_shape"
        and source_titles.get(
            str(dict(item["target"]).get("slide_signature"))
        )
        in str(dict(item["target"]).get("content_summary") or "")
    }
    expected_after = diff_preview(
        payload,
        include_proposed=False,
        source_changed=False,
    )["after"]
    remaining_titles = [
        str(item.get("title") or "").strip()
        for item in expected_after
        if item.get("original_index") is not None
        and str(item.get("stable_signature") or "") not in title_may_change
        and str(item.get("title") or "").strip()
        and str(item.get("title") or "").strip()
        in str(item.get("text_summary") or "")
    ]
    candidate_texts = [
        str(getattr(unit, "extracted_text", "")) for unit in units
    ]
    search_from = 0
    for title in remaining_titles:
        for index in range(search_from, len(candidate_texts)):
            if title in candidate_texts[index]:
                search_from = index + 1
                break
        else:
            raise TeachingPrepValidationError(
                "an unapproved source slide is missing or out of order"
            )


def _expected_protected_counts(
    payload: Mapping[str, object],
    *,
    source_units: tuple[object, ...] | None = None,
) -> dict[str, int]:
    deleted = {
        str(dict(item["target"]).get("slide_signature"))
        for item in payload["operations"]
        if item["decision"] == "approved" and item["kind"] == "delete_slide"
    }
    counts: Counter[str] = Counter()
    unit_by_page = {
        int(getattr(unit, "unit_index", 0)): unit
        for unit in (source_units or ())
    }
    slides_by_signature = {
        str(slide.get("stable_signature") or ""): slide
        for slide in payload["slides"]
    }
    for slide in payload["slides"]:
        signature = str(slide.get("stable_signature") or "")
        if signature in deleted:
            continue
        page = int(slide.get("original_index") or 0)
        source_unit = unit_by_page.get(page)
        summary = (
            getattr(source_unit, "object_summary", None)
            if source_unit is not None
            else slide.get("object_summary")
        )
        object_types = (
            dict(summary).get("object_types")
            if isinstance(summary, Mapping)
            else {}
        )
        if not isinstance(object_types, Mapping):
            continue
        for kind in (
            "pic",
            "graphicFrame",
            "grpSp",
            "oleObj",
            "video",
            "audio",
            "control",
        ):
            value = object_types.get(kind)
            if isinstance(value, int) and value > 0:
                counts[kind] += value
    for operation in payload["operations"]:
        if operation["decision"] != "approved" or operation["kind"] != "delete_shape":
            continue
        target = dict(operation["target"])
        signature = str(target.get("slide_signature") or "")
        slide = slides_by_signature.get(signature)
        if slide is None:
            continue
        source_unit = unit_by_page.get(int(slide.get("original_index") or 0))
        summary = getattr(source_unit, "object_summary", {}) if source_unit else {}
        objects = dict(summary).get("objects") if isinstance(summary, Mapping) else []
        match = next(
            (
                item
                for item in objects
                if isinstance(item, Mapping)
                and item.get("wps_object_id") == target.get("wps_object_id")
            ),
            None,
        )
        if match is None:
            continue
        object_type = str(match.get("object_type") or "")
        if object_type in counts:
            counts[object_type] -= 1
        descendants = match.get("protected_descendant_counts")
        if isinstance(descendants, Mapping):
            for kind, value in descendants.items():
                if isinstance(value, int) and value > 0:
                    counts[str(kind)] -= value
    inserted_images = sum(
        1
        for item in payload["operations"]
        if item["decision"] == "approved"
        and item["kind"] == "insert_static_image"
    )
    if inserted_images:
        counts["pic"] += inserted_images
    return dict(sorted((kind, value) for kind, value in counts.items() if value > 0))


def _actual_protected_counts(units: tuple[object, ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for unit in units:
        summary = getattr(unit, "object_summary", {})
        object_types = (
            summary.get("object_types") if isinstance(summary, Mapping) else {}
        )
        if not isinstance(object_types, Mapping):
            continue
        for kind in (
            "pic",
            "graphicFrame",
            "grpSp",
            "oleObj",
            "video",
            "audio",
            "control",
        ):
            value = object_types.get(kind)
            if isinstance(value, int) and value > 0:
                counts[kind] += value
    return dict(sorted(counts.items()))


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


__all__ = [
    "build_executor_request",
    "digest",
    "safe_execution_report",
    "verify_candidate",
]
