from __future__ import annotations

from answer_region_geometry import attach_region_source_image_sizes, scaled_region_bbox


def test_scaled_region_bbox_converts_template_coordinates_to_target_image_size() -> None:
    region = {
        "x": 1504,
        "y": 867,
        "w": 687,
        "h": 453,
        "source_image_width": 2831,
        "source_image_height": 1960,
    }

    assert scaled_region_bbox(region, 1768, 1224) == (939, 541, 1368, 824)


def test_scaled_region_bbox_keeps_legacy_coordinates_without_source_size() -> None:
    region = {"x": 10, "y": 20, "w": 120, "h": 80}

    assert scaled_region_bbox(region, 260, 180) == (10, 20, 130, 100)


def test_attach_region_source_image_sizes_uses_region_page() -> None:
    regions = [
        {"page": "front", "x": 1, "y": 2, "w": 3, "h": 4},
        {"page": "back", "x": 5, "y": 6, "w": 7, "h": 8},
    ]

    enriched = attach_region_source_image_sizes(regions, {"front": (2831, 1960), "back": (2800, 1900)})

    assert enriched[0]["source_image_width"] == 2831
    assert enriched[0]["source_image_height"] == 1960
    assert enriched[1]["source_image_width"] == 2800
    assert enriched[1]["source_image_height"] == 1900
    assert "source_image_width" not in regions[0]
