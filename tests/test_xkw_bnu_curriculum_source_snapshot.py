from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "xkw_bnu_math_2024_curriculum_2026-08-05.source.json"
)
_EXPECTED_VOLUMES = {
    "bnu24-math-g7-upper": 243,
    "bnu24-math-g7-lower": 185,
    "bnu24-math-g8-upper": 259,
    "bnu24-math-g8-lower": 229,
    "bnu24-math-g9-upper": 208,
}
_EXCLUDED_LABELS = {"回顾与思考", "复习题"}


def _walk_nodes(
    nodes: list[dict[str, Any]],
    *,
    expected_parent: str | None,
    expected_level: int,
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for expected_order, node in enumerate(nodes, start=1):
        assert node["parent_node_id"] == expected_parent
        assert node["order"] == expected_order
        assert node["level"] == expected_level
        assert len(node["path"]) == expected_level
        assert node["path"][-1] == node["label"]
        assert node["label"] not in _EXCLUDED_LABELS
        assert node["source_ref"]["node_id"]
        assert node["source_ref"]["relative_url"].startswith("/czsx/")
        flattened.append(node)
        flattened.extend(
            _walk_nodes(
                node["children"],
                expected_parent=node["node_id"],
                expected_level=expected_level + 1,
            )
        )
    return flattened


def test_xkw_bnu_curriculum_source_snapshot_is_complete_and_traceable() -> None:
    payload = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["status"] == "source_snapshot"
    assert payload["source"]["collection_mode"] == (
        "one_time_visible_browser_catalog_capture"
    )
    assert payload["source"]["runtime_refresh"] is False
    assert payload["retention_policy"]["excluded_labels"] == [
        "回顾与思考",
        "复习题",
    ]
    assert payload["statistics"] == {
        "volumes": 5,
        "raw_nodes": 1186,
        "excluded_nodes": 62,
        "retained_nodes": 1124,
        "excluded_by_label": {"回顾与思考": 31, "复习题": 31},
    }

    volumes = payload["volumes"]
    assert [volume["id"] for volume in volumes] == list(_EXPECTED_VOLUMES)

    all_nodes = 0
    for volume in volumes:
        assert volume["source"]["url"].startswith(
            "https://zujuan.xkw.com/czsx/zj"
        )
        flattened = _walk_nodes(
            volume["nodes"], expected_parent=None, expected_level=1
        )
        assert len(flattened) == _EXPECTED_VOLUMES[volume["id"]]
        assert len({node["node_id"] for node in flattened}) == len(flattened)
        assert len({tuple(node["path"]) for node in flattened}) == len(flattened)
        assert volume["statistics"]["retained_nodes"] == len(flattened)
        all_nodes += len(flattened)

    assert all_nodes == 1124
