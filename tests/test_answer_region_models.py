from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from answer_region_models import (
    EDGE_SNAP_TOLERANCE,
    MIN_REGION_SIZE,
    QuestionBindingCatalog,
    QuestionBindingOption,
    RegionIssue,
    RegionValidationResult,
    assign_sequential_question_ids,
    load_question_binding_catalog,
    normalize_regions,
    validate_regions,
)


IMAGE_SIZES = {"front": (100, 140), "back": (100, 140)}


def _region(
    region_uuid: str,
    *,
    page: str = "front",
    question_id: str | None = "Q1",
    x: int = 10,
    y: int = 10,
    w: int = 30,
    h: int = 30,
    mapping_status: str | None = None,
    multi_region_confirmed: bool = False,
) -> dict[str, object]:
    region: dict[str, object] = {
        "region_uuid": region_uuid,
        "page": page,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "mapped_question_id": question_id,
        "multi_region_confirmed": multi_region_confirmed,
    }
    if mapping_status is not None:
        region["mapping_status"] = mapping_status
    return region


def _issue_codes(result: RegionValidationResult) -> set[str]:
    return {issue.code for issue in result.issues}


def test_exposes_frozen_result_models_and_constants() -> None:
    issue = RegionIssue("unbound_question", "Question is unbound", "region-1", "Q1")
    result = RegionValidationResult((issue,))
    option = QuestionBindingOption("Q1", "Q1")
    catalog = QuestionBindingCatalog(("Q1",), (option,), frozenset())

    assert MIN_REGION_SIZE == 12
    assert EDGE_SNAP_TOLERANCE == 8
    assert result.can_commit is False
    assert RegionValidationResult(()).can_commit is True
    assert catalog.automatic_candidates == ("Q1",)
    with pytest.raises(FrozenInstanceError):
        issue.code = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.issues = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        option.label = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        catalog.automatic_candidates = ()  # type: ignore[misc]


def test_normalization_is_stable_copies_inputs_and_creates_blank_uuid() -> None:
    raw = {
        "region_uuid": " ",
        "page": "front",
        "region_order": "9",
        "x": 10.6,
        "y": "20",
        "w": 30.2,
        "h": 40.8,
        "mapped_question_id": "Q1",
        "is_confirmed": "0",
        "multi_region_confirmed": "true",
    }

    first = normalize_regions([raw])
    second = normalize_regions(first)

    assert raw["region_uuid"] == " "
    assert first == second
    assert first[0] is not raw
    assert first[0]["region_uuid"].strip()
    assert (first[0]["x"], first[0]["y"], first[0]["w"], first[0]["h"]) == (11, 20, 30, 41)
    assert first[0]["mapping_status"] == "manual"
    assert first[0]["is_confirmed"] is False
    assert first[0]["multi_region_confirmed"] is True


def test_normalization_orders_front_before_back_and_renumbers_regions() -> None:
    normalized = normalize_regions(
        [
            dict(_region("back", page="back"), region_order=1),
            dict(_region("front-two"), region_order=2),
            dict(_region("front-one"), region_order=1),
        ]
    )

    assert [region["region_uuid"] for region in normalized] == ["front-one", "front-two", "back"]
    assert [region["region_order"] for region in normalized] == [1, 2, 3]


def test_assigns_next_unused_scoring_units_without_rebinding_existing() -> None:
    regions = normalize_regions(
        [
            _region("manual", question_id="Q1", mapping_status="manual"),
            _region("auto", question_id="Q3(1)", mapping_status="auto"),
            _region("blank", question_id=" ", mapping_status="manual"),
            _region("unbound", question_id=None, mapping_status="unbound"),
        ]
    )

    assigned = assign_sequential_question_ids(regions, ["Q1", "Q2", "Q3(1)", "Q3(2)"])

    assert assigned is not regions
    assert [region["mapped_question_id"] for region in assigned] == ["Q1", "Q3(1)", "Q2", "Q3(2)"]
    assert [region["mapping_status"] for region in assigned] == ["manual", "auto", "auto", "auto"]
    assert regions[2]["mapped_question_id"] is None


def test_deleting_one_uuid_never_changes_other_region_identity() -> None:
    regions = normalize_regions(
        [
            _region("a", question_id=None, x=1, y=2, w=30, h=40),
            _region("b", question_id=None, x=50, y=60, w=30, h=40),
        ]
    )

    remaining = normalize_regions([region for region in regions if region["region_uuid"] != "a"])

    assert remaining == [dict(regions[1], region_order=1)]


def test_load_question_binding_catalog_uses_smallest_units_and_manual_parents(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1"},
                    {
                        "question_id": "Q10",
                        "parts": [{"part_id": "Q10(1)"}, {"part_id": "Q10(2)"}],
                    },
                    {"question_id": "__student_name__"},
                    {"question_id": "Q11", "parts": [{"part_id": "Q11-A"}]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    catalog = load_question_binding_catalog(rubric_path)
    options = {option.value: option.label for option in catalog.manual_options}

    assert catalog.automatic_candidates == ("Q1", "Q10(1)", "Q10(2)", "Q11-A")
    assert catalog.parent_question_ids == frozenset({"Q10"})
    assert [option.value for option in catalog.manual_options] == [
        "",
        "__student_name__",
        "Q1",
        "Q10",
        "Q10(1)",
        "Q10(2)",
        "Q11-A",
    ]
    assert options[""] == ""
    assert options["__student_name__"] == "姓名识别区域"
    assert options["Q10"] == "Q10（整道大题）"


def test_load_question_binding_catalog_falls_back_for_invalid_utf8(tmp_path: Path) -> None:
    rubric_path = tmp_path / "invalid-rubric.json"
    rubric_path.write_bytes(b"\xff\xfe\xfa")

    catalog = load_question_binding_catalog(rubric_path)

    assert catalog == load_question_binding_catalog(tmp_path / "missing-rubric.json")


def test_template_mismatch_blocks_commit() -> None:
    result = validate_regions(
        normalize_regions([_region("a")]),
        image_sizes=IMAGE_SIZES,
        template_matches=False,
    )

    assert "template_mismatch" in _issue_codes(result)


def test_template_mismatch_does_not_clamp_region_coordinates() -> None:
    region = _region("a", x=-EDGE_SNAP_TOLERANCE, y=20, w=40, h=40)
    coordinates_before = {key: region[key] for key in ("x", "y", "w", "h")}

    result = validate_regions([region], image_sizes=IMAGE_SIZES, template_matches=False)

    assert "template_mismatch" in _issue_codes(result)
    assert {key: region[key] for key in ("x", "y", "w", "h")} == coordinates_before


def test_unbound_region_blocks_commit() -> None:
    result = validate_regions(
        normalize_regions([_region("a", question_id=None)]),
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert "unbound_question" in _issue_codes(result)


def test_region_smaller_than_minimum_blocks_commit() -> None:
    result = validate_regions(
        normalize_regions([_region("a", w=MIN_REGION_SIZE - 1)]),
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert "region_too_small" in _issue_codes(result)


def test_region_clearly_outside_image_blocks_commit() -> None:
    result = validate_regions(
        normalize_regions([_region("a", x=-(EDGE_SNAP_TOLERANCE + 1))]),
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert "region_out_of_bounds" in _issue_codes(result)


def test_region_lightly_outside_image_is_clamped_and_accepted() -> None:
    regions = normalize_regions(
        [
            _region(
                "a",
                x=-EDGE_SNAP_TOLERANCE,
                y=20,
                w=40,
                h=40,
            )
        ]
    )

    result = validate_regions(regions, image_sizes=IMAGE_SIZES, template_matches=True)

    assert result.can_commit is True
    assert regions[0]["x"] == 0
    assert regions[0]["x"] + regions[0]["w"] <= IMAGE_SIZES["front"][0]


def test_validate_regions_safely_converts_direct_numeric_coordinates() -> None:
    region = _region("a")
    region.update({"x": "-8.0", "y": 20.4, "w": "40.2", "h": 40.6})

    result = validate_regions([region], image_sizes=IMAGE_SIZES, template_matches=True)

    assert result.can_commit is True
    assert (region["x"], region["y"], region["w"], region["h"]) == (0, 20, 32, 41)


def test_validate_regions_reports_invalid_geometry_without_crashing() -> None:
    region = _region("a")
    region.update({"x": "not-a-number", "w": None})

    result = validate_regions([region], image_sizes=IMAGE_SIZES, template_matches=True)

    assert {"region_out_of_bounds", "region_too_small"} <= _issue_codes(result)
    assert region["x"] == "not-a-number"
    assert region["w"] is None


def test_validate_regions_reports_overflowing_geometry_without_crashing() -> None:
    region = _region("a")
    region["x"] = 10**10_000

    result = validate_regions([region], image_sizes=IMAGE_SIZES, template_matches=True)

    assert "region_out_of_bounds" in _issue_codes(result)


def test_duplicate_uuid_blocks_commit() -> None:
    result = validate_regions(
        normalize_regions([_region("same", question_id="Q1"), _region("same", question_id="Q2", y=50)]),
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert "duplicate_uuid" in _issue_codes(result)


def test_unconfirmed_multi_region_group_blocks_commit() -> None:
    result = validate_regions(
        normalize_regions(
            [
                _region("a", question_id="Q1", multi_region_confirmed=True),
                _region("b", page="back", question_id="Q1", multi_region_confirmed=False),
            ]
        ),
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    issues = [issue for issue in result.issues if issue.code == "unconfirmed_multi_region"]
    assert result.can_commit is False
    assert len(issues) == 1
    assert issues[0].question_id == "Q1"


def test_confirmed_multi_region_group_passes_validation() -> None:
    result = validate_regions(
        normalize_regions(
            [
                _region("a", question_id="Q1", multi_region_confirmed=True),
                _region("b", page="back", question_id="Q1", multi_region_confirmed=True),
            ]
        ),
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert result.can_commit is True
