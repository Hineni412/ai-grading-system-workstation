from __future__ import annotations

import pytest

from major_region_evidence import build_major_evidence_groups


def _region(
    question_id: str,
    *,
    page: str = "front",
    x: float,
    y: float,
    w: float,
    h: float,
    source_width: int | None = None,
    source_height: int | None = None,
) -> dict:
    item = {
        "page": page,
        "mapped_question_id": question_id,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
    }
    if source_width is not None:
        item["source_image_width"] = source_width
    if source_height is not None:
        item["source_image_height"] = source_height
    return item


def _part_map(groups: list[dict]) -> dict[tuple[str, tuple[str, ...]], dict]:
    return {
        (str(group["page"]), tuple(group["part_ids"])): group
        for group in groups
    }


def test_nested_same_page_regions_become_one_group_in_rubric_order() -> None:
    groups = build_major_evidence_groups(
        "Q10",
        ["10-1", "10-2", "10-3"],
        [
            _region("Q10(3)", x=30, y=30, w=160, h=160, source_width=2831, source_height=1960),
            _region("Q10(1)", x=10, y=10, w=200, h=200, source_width=2831, source_height=1960),
            _region("Q10(2)", x=20, y=20, w=180, h=180, source_width=2831, source_height=1960),
        ],
    )

    assert groups == [
        {
            "page": "front",
            "bbox": {
                "x": 10,
                "y": 10,
                "w": 200,
                "h": 200,
                "source_image_width": 2831,
                "source_image_height": 1960,
            },
            "part_ids": ["10-1", "10-2", "10-3"],
        }
    ]


def test_separate_part_plus_nearly_identical_pair_becomes_two_groups() -> None:
    groups = build_major_evidence_groups(
        "Q12",
        ["12-1", "12-2", "12-3"],
        [
            _region("Q12(1)", x=10, y=10, w=80, h=80),
            _region("Q12(2)", x=200, y=10, w=100, h=100),
            _region("Q12(3)", x=200.2, y=10, w=100, h=100),
        ],
    )

    assert groups == [
        {
            "page": "front",
            "bbox": {"x": 10, "y": 10, "w": 80, "h": 80},
            "part_ids": ["12-1"],
        },
        {
            "page": "front",
            "bbox": {"x": 200, "y": 10, "w": pytest.approx(100.2), "h": 100},
            "part_ids": ["12-2", "12-3"],
        },
    ]


def test_low_overlap_regions_stay_separate() -> None:
    groups = build_major_evidence_groups(
        "Q11",
        ["11-1", "11-2"],
        [
            _region("Q11(1)", x=10, y=10, w=100, h=100),
            _region("Q11(2)", x=70, y=10, w=100, h=100),
        ],
    )

    assert groups == [
        {
            "page": "front",
            "bbox": {"x": 10, "y": 10, "w": 100, "h": 100},
            "part_ids": ["11-1"],
        },
        {
            "page": "front",
            "bbox": {"x": 70, "y": 10, "w": 100, "h": 100},
            "part_ids": ["11-2"],
        },
    ]


def test_parent_regions_only_fill_unresolved_parts_across_pages() -> None:
    groups = build_major_evidence_groups(
        "Q12",
        ["12-1", "12-2", "12-3"],
        [
            _region("Q12(1)", page="front", x=10, y=10, w=80, h=80),
            _region("Q12", page="back", x=100, y=120, w=160, h=140),
        ],
    )

    assert groups == [
        {
            "page": "front",
            "bbox": {"x": 10, "y": 10, "w": 80, "h": 80},
            "part_ids": ["12-1"],
        },
        {
            "page": "back",
            "bbox": {"x": 100, "y": 120, "w": 160, "h": 140},
            "part_ids": ["12-2", "12-3"],
        },
    ]


def test_parent_only_regions_remain_split_by_page_and_map_all_parts() -> None:
    groups = build_major_evidence_groups(
        "Q10",
        ["10-1", "10-2"],
        [
            _region("Q10", page="front", x=10, y=10, w=100, h=100),
            _region("Q10", page="back", x=20, y=20, w=120, h=140),
        ],
    )

    assert groups == [
        {
            "page": "front",
            "bbox": {"x": 10, "y": 10, "w": 100, "h": 100},
            "part_ids": ["10-1", "10-2"],
        },
        {
            "page": "back",
            "bbox": {"x": 20, "y": 20, "w": 120, "h": 140},
            "part_ids": ["10-1", "10-2"],
        },
    ]


def test_same_explicit_part_on_two_pages_stays_as_two_groups() -> None:
    groups = build_major_evidence_groups(
        "Q10",
        ["10-1"],
        [
            _region("Q10(1)", page="front", x=10, y=10, w=100, h=100),
            _region("Q10(1)", page="back", x=20, y=20, w=100, h=100),
        ],
    )

    assert groups == [
        {
            "page": "front",
            "bbox": {"x": 10, "y": 10, "w": 100, "h": 100},
            "part_ids": ["10-1"],
        },
        {
            "page": "back",
            "bbox": {"x": 20, "y": 20, "w": 100, "h": 100},
            "part_ids": ["10-1"],
        },
    ]
