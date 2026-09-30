"""Read-only local OCR suggestions for the existing answer-region draft editor."""
from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

import numpy as np
from PIL import Image

from answer_region_models import MIN_REGION_SIZE, QuestionBindingCatalog, normalize_regions
from original_paper_exporter import printed_question_candidates, read_ocr_lines
from question_id_contract import question_id_coordinates

NAME_ID = "__student_name__"
_NAME_FOLLOWING_LABELS = ("班级", "考号", "学号", "座号")


def propose_answer_regions(
    page_paths: dict[str, Path], catalog: QuestionBindingCatalog, *, ocr_engine: Any = None,
) -> dict[str, Any]:
    """Compute suggestions without changing templates, drafts or the database."""
    if ocr_engine is None:
        from local_ocr import get_local_ocr

        ocr_engine = get_local_ocr()
    bindings = {}
    for value in catalog.automatic_candidates:
        coordinates = question_id_coordinates(value)
        if coordinates is not None:
            parent = f"Q{coordinates[0]}"
            bindings[parent] = parent if parent in catalog.parent_question_ids else value
    parents = set(bindings)
    pages = {}
    ordered_candidates = []
    for page in ("front", "back"):
        path = page_paths.get(page)
        if path is None:
            continue
        with Image.open(path) as source:
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        height, width = image.shape[:2]
        lines = read_ocr_lines(image, page=page, ocr_engine=ocr_engine)
        candidates = printed_question_candidates(lines, parents)
        columns = _columns(candidates, width)
        for column, (left, right) in enumerate(columns):
            strip_right = min(width, left + round((right - left) * 0.35))
            strip_lines = read_ocr_lines(
                image[:, left:strip_right].copy(), page=page, ocr_engine=ocr_engine,
                source_size=(width, height), offset=(left, 0),
            )
            candidates.extend(printed_question_candidates(strip_lines, parents))
        # Keep full-page text for column edges; narrow crops truncate ordinary text.
        if page == "front" and not any("姓名" in line["text"] and line["confidence"] >= 0.55 for line in lines):
            lines.extend(read_ocr_lines(
                image[:max(1, height // 4)].copy(), page=page, ocr_engine=ocr_engine,
                source_size=(width, height),
            ))
        pages[page] = {"size": (width, height), "lines": lines, "columns": columns}
        for column, (left, right) in enumerate(columns):
            group = [dict(item, column=column) for item in candidates
                     if left <= item["x"] <= left + (right - left) * 0.25]
            merged = []
            for item in sorted(group, key=lambda item: (-item["confidence"], item["x"])):
                if any(item["question_id"] == other["question_id"]
                       and abs(item["y"] - other["y"]) <= max(12, item["h"], other["h"])
                       for other in merged):
                    continue
                merged.append(item)
            ordered_candidates.extend(sorted(merged, key=lambda item: (item["y"], item["x"])))

    anchors = _increasing_anchors(ordered_candidates)
    regions = []
    for page, data in pages.items():
        width, height = data["size"]
        margin = max(4, round(width * 0.012))
        for column, (left, right) in enumerate(data["columns"]):
            group = [item for item in anchors if item["page"] == page and item["column"] == column]
            if not group:
                continue
            column_lines = [line for line in data["lines"] if left <= line["x"] < right
                            and line["x"] + line["w"] <= right + margin]
            x = max(left, min(item["x"] for item in group) - margin)
            edge = min(right - margin, max(
                (line["x"] + line["w"] for line in column_lines), default=right - margin,
            ))
            edge = max(x + MIN_REGION_SIZE, edge)
            for index, anchor in enumerate(group):
                top = max(0, anchor["y"] - 4)
                bottom = group[index + 1]["y"] if index + 1 < len(group) else max(
                    height - margin,
                    max((line["y"] + line["h"] for line in column_lines), default=0),
                )
                regions.append(_region(anchor, bindings[anchor["question_id"]],
                                       (x, top, edge, bottom), data["size"]))

    _append_cross_page_region(regions, anchors, pages, bindings)
    name = _name_region(pages.get("front"))
    if name is not None and anchors:
        regions.insert(0, name)
    found = {item["mapped_question_id"] for item in regions}
    expected = [bindings[parent] for parent in sorted(parents, key=lambda parent: int(parent[1:]))]
    return {
        "regions": normalize_regions(regions),
        "missing_question_ids": [value for value in [*expected, NAME_ID] if value not in found],
    }


def _columns(candidates: list[dict[str, Any]], width: int) -> list[tuple[int, int]]:
    clusters: list[list[int]] = []
    for item in sorted(candidates, key=lambda item: item["x"]):
        x = item["x"]
        if clusters and x - median(clusters[-1]) <= width * 0.08:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    left_groups = [group for group in clusters if median(group) <= width * 0.25]
    right_groups = [group for group in clusters if width * 0.42 <= median(group) <= width * 0.75]
    if left_groups and right_groups:
        left = max(left_groups, key=len)
        right = max(right_groups, key=len)
        if median(right) - median(left) >= width * 0.28:
            split = round(median(right) - min(median(left), width * 0.08))
            left_lines = [item for item in candidates if abs(item["x"] - median(left)) <= width * 0.08]
            # A full-width question line rules out a body number masquerading as a right column.
            if not any(item["x"] + item["w"] > split + width * 0.04 for item in left_lines):
                return [(0, split), (split, width)]
    return [(0, width)]


def _increasing_anchors(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Longest strictly increasing sequence in page/column reading order.
    chains: list[list[dict[str, Any]]] = []
    for item in candidates:
        number = int(item["question_id"][1:])
        preceding = [chain for chain in chains if int(chain[-1]["question_id"][1:]) < number]
        best = max(preceding, key=_chain_quality, default=[])
        chains.append([*best, item])
    return max(chains, key=_chain_quality, default=[])


def _chain_quality(chain: list[dict[str, Any]]) -> tuple[int, float, int]:
    return len(chain), sum(item["confidence"] for item in chain), -sum(item["x"] for item in chain)


def _region(anchor: dict[str, Any], binding: str, bounds: tuple[float, float, float, float],
            size: tuple[int, int]) -> dict[str, Any]:
    width, height = size
    left, top, right, bottom = [round(value) for value in bounds]
    left = max(0, min(width - MIN_REGION_SIZE, left))
    top = max(0, min(height - MIN_REGION_SIZE, top))
    right = max(left + MIN_REGION_SIZE, min(width, right))
    bottom = max(top + MIN_REGION_SIZE, min(height, bottom))
    return {
        "region_uuid": str(uuid4()), "page": anchor["page"], "region_order": 0,
        "x": left, "y": top, "w": right - left, "h": bottom - top,
        "mapped_question_id": binding, "mapping_status": "auto", "is_confirmed": False,
        "multi_region_confirmed": False, "detected_question_id": anchor.get("question_id", binding),
        "confidence": anchor["confidence"],
    }


def _name_region(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    lines = data["lines"]
    labels = [line for line in lines if "姓名" in line["text"] and line["confidence"] >= 0.55]
    if not labels:
        return None
    label = min(labels, key=lambda line: (line["y"], -line["confidence"]))
    text = label["text"]
    start = text.index("姓名")
    char_width = label["w"] / max(1, len(text))
    x = label["x"] + start * char_width
    following = [text.index(value, start + 2) for value in _NAME_FOLLOWING_LABELS
                 if value in text[start + 2:]]
    label_width = label["w"] if len(text.strip()) <= 4 else char_width * 3
    edge = label["x"] + min(following) * char_width if following else x + label_width * 4
    others = [line["x"] for line in lines if line is not label and line["x"] > x
              and abs(line["y"] + line["h"] / 2 - label["y"] - label["h"] / 2)
              <= max(line["h"], label["h"])
              and any(value in line["text"] for value in _NAME_FOLLOWING_LABELS)]
    if others:
        edge = min(edge, min(others)) if following else min(others)
    return _region(label, NAME_ID,
                   (x - 6, label["y"] - label["h"] / 2, edge - 3, label["y"] + label["h"] * 1.5),
                   data["size"])


def _append_cross_page_region(regions: list[dict[str, Any]], anchors: list[dict[str, Any]],
                              pages: dict[str, Any], bindings: dict[str, str]) -> None:
    front = [item for item in anchors if item["page"] == "front"]
    back = pages.get("back")
    if not front or back is None:
        return
    first = next((item for item in anchors if item["page"] == "back"), None)
    # A continuation may occupy the whole back page when it has no new number.
    column = first["column"] if first else 0
    left, right = back["columns"][column]
    stop = first["y"] if first else back["size"][1] - 12
    lines = [line for line in back["lines"] if left <= line["x"] < right
             and line["y"] + line["h"] <= stop and line["y"] >= 12
             and not any(value in line["text"] for value in ("姓名", *_NAME_FOLLOWING_LABELS))]
    if not lines:
        return
    last = front[-1]
    x = max(left, min(line["x"] for line in lines) - 8)
    edge = min(right - 8, max(line["x"] + line["w"] for line in lines))
    regions.append(_region(dict(last, page="back"), bindings[last["question_id"]],
                           (x, min(line["y"] for line in lines) - 4, edge, stop), back["size"]))
