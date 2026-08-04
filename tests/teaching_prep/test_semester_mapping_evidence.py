from __future__ import annotations

import pytest

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.application.semester_mapping import (
    validate_semester_mapping_payload,
)
from backend.teaching_prep.application.semester_mapping_evidence import (
    build_directory_evidence,
)
from backend.teaching_prep.infrastructure.llm.semester_mapping import (
    _compact_model_snapshot,
)


def _snapshot() -> dict[str, object]:
    units: list[dict[str, object]] = []
    for index in range(1, 68):
        text = f"第 {index} 页练习内容"
        if index == 2:
            text = "目录\n第一章 勾股定理 ...... 1\n1.1 探索勾股定理 ...... 3\n1.2 勾股定理应用 ...... 9"
        if index == 7:
            text = "第一章 勾股定理"
        if index == 9:
            text = "1.1 探索勾股定理"
        if index == 15:
            text = "1.2 勾股定理应用"
        units.append(
            {"unit_index": index, "title": None, "text_excerpt": text}
        )
    return {
        "semester": {"school_year": "2026", "term": "上学期"},
        "lessons": [],
        "materials": [
            {
                "record_id": "record-1",
                "display_name": "测试教辅",
                "material_role": "exercise_workbook",
                "unit_count": 67,
                "units": units,
            }
        ],
    }


def test_directory_evidence_calibrates_toc_without_sending_all_pages() -> None:
    snapshot = _snapshot()
    evidence = build_directory_evidence(snapshot)
    model_snapshot = {**snapshot, "directory_evidence": evidence}
    compact = _compact_model_snapshot(model_snapshot)

    assert evidence["strategy"] == "toc_calibrated"
    assert evidence["printed_to_pdf_offset"] == 6
    assert evidence["full_page_text_sent"] is False
    assert len(evidence["scanned_unit_indices"]) == 12
    assert "units" not in compact["materials"][0]
    assert compact["directory_evidence"]["anchors"]


def test_mapping_validation_adds_basis_and_merges_adjacent_pages() -> None:
    snapshot = _snapshot()
    snapshot["directory_evidence"] = build_directory_evidence(snapshot)
    raw = {
        "tree": [
            {
                "key": "chapter-a",
                "title": "第一章",
                "sections": [
                    {
                        "key": "section-a",
                        "title": "勾股定理",
                        "lessons": [
                            {
                                "key": "lesson-a",
                                "title": "探索勾股定理",
                                "duration_minutes": 45,
                            }
                        ],
                    }
                ],
            }
        ],
        "mappings": [
            {
                "material_record_id": "record-1",
                "lesson_ref": "proposal:lesson-a",
                "start_unit": 9,
                "end_unit": 9,
                "basis": "正文标题与目录一致",
                "evidence_refs": ["toc-002", "anchor-0009"],
            },
            {
                "material_record_id": "record-1",
                "lesson_ref": "proposal:lesson-a",
                "start_unit": 10,
                "end_unit": 12,
                "basis": "连续练习页",
                "evidence_refs": ["range-002"],
            },
        ],
        "uncertainties": [],
    }

    result = validate_semester_mapping_payload(raw, snapshot=snapshot)

    assert len(result["mappings"]) == 1
    assert result["mappings"][0]["start_unit"] == 9
    assert result["mappings"][0]["end_unit"] == 12
    assert "正文标题与目录一致" in result["mappings"][0]["basis"]
    assert result["mappings"][0]["evidence_refs"]


def test_directory_search_expands_when_toc_starts_after_initial_window() -> None:
    snapshot = _snapshot()
    units = list(snapshot["materials"][0]["units"])
    units.extend(
        {"unit_index": index, "title": None, "text_excerpt": f"练习 {index}"}
        for index in range(68, 101)
    )
    units[1]["text_excerpt"] = "前言"
    units[16]["text_excerpt"] = "目录\n第一章 三角形 ...... 1"
    units[29]["text_excerpt"] = "第一章 三角形"
    snapshot["materials"][0]["units"] = units
    snapshot["materials"][0]["unit_count"] = 100

    evidence = build_directory_evidence(snapshot)

    assert evidence["strategy"] != "sparse_outline"
    assert len(evidence["scanned_unit_indices"]) == 22
    assert evidence["toc_entries"][0]["source_unit"] == 17


def test_mapping_validation_rejects_invented_evidence_reference() -> None:
    snapshot = _snapshot()
    snapshot["directory_evidence"] = build_directory_evidence(snapshot)
    raw = {
        "tree": [{
            "key": "chapter-a", "title": "第一章", "sections": [{
                "key": "section-a", "title": "第一节", "lessons": [{
                    "key": "lesson-a", "title": "第1课时", "duration_minutes": 45,
                }],
            }],
        }],
        "mappings": [{
            "material_record_id": "record-1",
            "lesson_ref": "proposal:lesson-a",
            "start_unit": 9,
            "end_unit": 12,
            "basis": "目录与正文一致",
            "evidence_refs": ["toc-invented"],
        }],
        "uncertainties": [],
    }

    with pytest.raises(TeachingPrepValidationError, match="unavailable directory evidence"):
        validate_semester_mapping_payload(raw, snapshot=snapshot)


def test_existing_tree_mapping_rejects_silent_unmapped_pages() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-existing",
            "node_type": "lesson",
            "title": "探索勾股定理",
        }
    ]

    with pytest.raises(
        TeachingPrepValidationError,
        match="omitted uncertainty for unmapped pages",
    ):
        validate_semester_mapping_payload(
            {
                "tree": [],
                "mappings": [],
                "uncertainties": [],
            },
            snapshot=snapshot,
        )


def test_existing_tree_mapping_allows_unmapped_pages_with_uncertainty() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-existing",
            "node_type": "lesson",
            "title": "探索勾股定理",
        }
    ]

    result = validate_semester_mapping_payload(
        {
            "tree": [],
            "mappings": [],
            "uncertainties": ["当前资料没有足够证据对应到已有课时。"],
        },
        snapshot=snapshot,
    )

    assert result["mappings"] == []
    assert result["uncertainties"] == [
        "当前资料没有足够证据对应到已有课时。"
    ]
