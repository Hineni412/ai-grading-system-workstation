from __future__ import annotations

import pytest

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.application.semester_mapping import (
    materialize_semantic_mapping_payload,
    rematerialize_existing_mapping_payload,
    validate_semester_mapping_payload,
)
from backend.teaching_prep.application.semester_mapping_evidence import (
    _choose_printed_offset,
    _heading_level,
    _repair_toc_printed_pages,
    _select_printed_offset,
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


def test_directory_evidence_parses_flat_ocr_slash_page_references() -> None:
    snapshot = _snapshot()
    units = list(snapshot["materials"][0]["units"])
    units[1]["text_excerpt"] = "前言"
    units[4]["text_excerpt"] = (
        "目录\n第一章\n1探索勾股定理/2\n"
        "2一定是直角三角形吗／10\n3勾股定理的应用/13"
    )
    units[5]["text_excerpt"] = (
        "第四章\n1函数/75\n2\n认识一次函数/79"
    )
    units[9]["text_excerpt"] = "探索勾股定理"
    units[11]["text_excerpt"] = "数学八年级上册/12"
    units[16]["text_excerpt"] = "数学八年级上册/17"
    units[17]["text_excerpt"] = "一定是直角三角形吗"
    units[19]["text_excerpt"] = "数学八年级上册/20"
    units[20]["text_excerpt"] = "勾股定理的应用"

    evidence = build_directory_evidence(snapshot)

    assert evidence["strategy"] == "toc_calibrated"
    assert evidence["printed_to_pdf_offset"] == 8
    assert evidence["directory_page_unit_indices"] == [5, 6]
    assert [item["printed_page"] for item in evidence["toc_entries"][:3]] == [
        2,
        10,
        13,
    ]


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


def test_directory_evidence_rebuilds_spread_rows_from_ocr_coordinates() -> None:
    snapshot = _snapshot()
    snapshot["materials"][0]["display_name"] = "全品八上作业册"
    units = list(snapshot["materials"][0]["units"])
    units[1]["text_excerpt"] = "前言"
    units[2]["text_excerpt"] = "目录\n第1课时\n听2\n作1"
    units[2]["object_summary"] = {
        "ocr_layout": {
            "version": 1,
            "items": [
                _layout_item("目录", 0.04, 0.03, 0.12, 0.08),
                _layout_item("第1课时 探索勾股定理", 0.06, 0.20, 0.34, 0.23),
                _layout_item("听2", 0.39, 0.20, 0.43, 0.23),
                _layout_item("作1", 0.45, 0.20, 0.49, 0.23),
                _layout_item("第2课时 勾股定理应用", 0.06, 0.26, 0.34, 0.29),
                _layout_item("听6", 0.39, 0.26, 0.43, 0.29),
                _layout_item("作3", 0.45, 0.26, 0.49, 0.29),
                _layout_item("第1课时 确定位置", 0.56, 0.20, 0.79, 0.23),
                _layout_item("听46", 0.88, 0.20, 0.92, 0.23),
                _layout_item("作37", 0.94, 0.20, 0.98, 0.23),
            ],
        },
    }
    units[3]["text_excerpt"] = "第2课时\n听104\n作89\n第一章正文"
    units[3]["object_summary"] = {
        "ocr_layout": {
            "version": 1,
                "items": [
                    _layout_item("第2课时 三元一次方程组", 0.06, 0.12, 0.34, 0.15),
                    _layout_item("听104", 0.39, 0.12, 0.43, 0.15),
                    _layout_item("作89", 0.45, 0.12, 0.49, 0.15),
                    _layout_item("问题解决策略", 0.06, 0.18, 0.34, 0.21),
                    _layout_item("听107", 0.39, 0.18, 0.43, 0.21),
                    _layout_item("作93", 0.45, 0.18, 0.49, 0.21),
                    _layout_item("本章中考演练", 0.06, 0.24, 0.34, 0.27),
                    _layout_item("作94", 0.45, 0.24, 0.49, 0.27),
                    _layout_item("1 第1课时 探索勾股定理", 0.56, 0.20, 0.92, 0.24),
                    _layout_item("作1", 0.94, 0.20, 0.98, 0.24),
                ],
        },
    }

    evidence = build_directory_evidence(snapshot)

    assert evidence["strategy"] != "sparse_outline"
    assert evidence["directory_page_unit_indices"] == [3, 4]
    assert [item["title"] for item in evidence["toc_entries"]] == [
        "第1课时 探索勾股定理",
        "第2课时 勾股定理应用",
        "第1课时 确定位置",
        "第2课时 三元一次方程组",
        "问题解决策略",
        "本章中考演练",
    ]
    assert [item["printed_page"] for item in evidence["toc_entries"]] == [
        1,
        3,
        37,
        89,
        93,
        94,
    ]
    assert evidence["toc_entries"][0]["page_refs"] == {"听": 2, "作": 1}


def test_directory_evidence_supports_plain_workbook_and_textbook_layouts() -> None:
    snapshot = _snapshot()
    units = list(snapshot["materials"][0]["units"])
    units[1]["text_excerpt"] = "目录"
    units[1]["object_summary"] = {
        "width": 1000,
        "height": 1600,
        "ocr_layout": {
            "version": 1,
            "items": [
                _layout_item("目录", 0.08, 0.04, 0.20, 0.08),
                _layout_item("第1课时 探索勾股定理", 0.08, 0.18, 0.66, 0.21),
                _layout_item("1", 0.91, 0.18, 0.95, 0.21),
                _layout_item("第2课时 勾股定理的验证", 0.08, 0.24, 0.70, 0.27),
                _layout_item("2", 0.91, 0.24, 0.95, 0.27),
                _layout_item("小专题 方程思想", 0.08, 0.30, 0.54, 0.33),
                _layout_item("8", 0.91, 0.30, 0.95, 0.33),
            ],
        },
    }
    units[2]["text_excerpt"] = "目录续页"
    units[2]["object_summary"] = {
        "width": 1000,
        "height": 1600,
        "ocr_layout": {
            "version": 1,
            "items": [
                _layout_item("1 函数 / 75", 0.35, 0.12, 0.66, 0.15),
                _layout_item("2 认识一次函数 / 79", 0.35, 0.18, 0.72, 0.21),
                _layout_item("回顾与思考 / 105", 0.35, 0.24, 0.70, 0.27),
            ],
        },
    }
    snapshot["materials"][0]["units"] = units

    evidence = build_directory_evidence(snapshot)

    assert [item["title"] for item in evidence["toc_entries"]] == [
        "第1课时 探索勾股定理",
        "第2课时 勾股定理的验证",
        "小专题 方程思想",
        "1 函数",
        "2 认识一次函数",
        "回顾与思考",
    ]
    assert [item["printed_page"] for item in evidence["toc_entries"]] == [
        1,
        2,
        8,
        75,
        79,
        105,
    ]


def test_directory_evidence_keeps_unpaged_headings_and_parallel_aux_rows() -> None:
    snapshot = _snapshot()
    snapshot["materials"][0]["display_name"] = "全品八上作业册"
    units = list(snapshot["materials"][0]["units"])
    units[1]["text_excerpt"] = "目录"
    units[1]["object_summary"] = {
        "width": 1600,
        "height": 1000,
        "ocr_layout": {
            "version": 1,
            "items": [
                _layout_item("目录", 0.04, 0.03, 0.12, 0.07),
                _layout_item("1 探索勾股定理", 0.06, 0.16, 0.26, 0.19),
                _layout_item("第1课时 探索勾股定理", 0.06, 0.20, 0.31, 0.23),
                _layout_item("听2", 0.39, 0.20, 0.43, 0.23),
                _layout_item("作1", 0.45, 0.20, 0.49, 0.23),
                _layout_item(
                    "周末自评（一）[范围：第一章]/评1",
                    0.06,
                    0.78,
                    0.25,
                    0.81,
                ),
                _layout_item(
                    "周末自评（七）[范围：第四章]/评13",
                    0.27,
                    0.78,
                    0.47,
                    0.81,
                ),
                _layout_item("参考答案/活15", 0.06, 0.86, 0.20, 0.89),
                _layout_item("平均数与方差", 0.56, 0.16, 0.70, 0.19),
                _layout_item("第1课时 众数和算术平均数", 0.56, 0.20, 0.80, 0.23),
                _layout_item("听111", 0.89, 0.20, 0.93, 0.23),
                _layout_item("作97", 0.94, 0.20, 0.98, 0.23),
            ],
        },
    }
    snapshot["materials"][0]["units"] = units

    evidence = build_directory_evidence(snapshot)

    assert [item["title"] for item in evidence["toc_entries"]] == [
        "1 探索勾股定理",
        "第1课时 探索勾股定理",
        "周末自评（一）[范围：第一章]",
        "周末自评（七）[范围：第四章]",
        "参考答案",
        "平均数与方差",
        "第1课时 众数和算术平均数",
    ]
    assert [item["page_refs"] for item in evidence["toc_entries"]] == [
        {},
        {"听": 2, "作": 1},
        {"评": 1},
        {"评": 13},
        {"活": 15},
        {},
        {"听": 111, "作": 97},
    ]


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


def test_semantic_mapping_materializes_page_ranges_from_local_evidence() -> None:
    snapshot = _snapshot()
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": (
                "第1课时 探索勾股定理"
                if index == 1
                else str(item["title"])
            ),
            "chapter_title": "第一章 勾股定理",
            "section_title": "第一节 探索勾股定理",
            "kind": (
                "chapter" if index == 0 else "lesson" if index == 1 else "special"
            ),
        }
        for index, item in enumerate(evidence["toc_entries"])
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": annotations,
            "matches": [],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )
    result = validate_semester_mapping_payload(
        materialized,
        snapshot=snapshot,
    )

    assert len(result["tree"]) == 1
    assert len(result["tree"][0]["sections"][0]["lessons"]) == 1
    assert result["mappings"]
    assert result["mappings"][0]["start_unit"] == 9
    assert result["mappings"][0]["start_unit"] == next(
        item["start_unit"]
        for item in evidence["resolved_ranges"]
        if item["toc_evidence_id"] == "toc-002"
    )
    assert result["mappings"][0]["evidence_refs"][:2] == [
        "toc-002",
        "range-002",
    ]


def test_semantic_mapping_rejects_model_page_fields() -> None:
    snapshot = _snapshot()
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": str(item["title"]),
            "chapter_title": "第一章",
            "section_title": "第一节",
            "kind": "lesson",
        }
        for item in evidence["toc_entries"]
    ]
    annotations[0]["printed_page"] = 999

    with pytest.raises(
        TeachingPrepValidationError,
        match="forbidden fields",
    ):
        materialize_semantic_mapping_payload(
            {
                "annotations": annotations,
                "matches": [],
                "uncertainties": [],
            },
            snapshot=snapshot,
        )


def test_semantic_mapping_rejects_incomplete_directory_coverage() -> None:
    snapshot = _snapshot()
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence

    with pytest.raises(
        TeachingPrepValidationError,
        match="did not cover every local directory row",
    ):
        materialize_semantic_mapping_payload(
            {
                "annotations": [],
                "matches": [],
                "uncertainties": [],
            },
            snapshot=snapshot,
        )


def test_semantic_mapping_does_not_count_a_section_as_a_lesson() -> None:
    snapshot = _snapshot()
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": str(item["title"]),
            "chapter_title": "第一章",
            "section_title": "第一节",
            "kind": "lesson" if index == 1 else "section",
        }
        for index, item in enumerate(evidence["toc_entries"])
    ]

    with pytest.raises(
        TeachingPrepValidationError,
        match="not an explicit numbered lesson",
    ):
        materialize_semantic_mapping_payload(
            {
                "annotations": annotations,
                "matches": [],
                "uncertainties": [],
            },
            snapshot=snapshot,
        )


def test_semantic_mapping_allows_one_section_for_split_existing_lessons() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "chapter-pythagoras",
            "parent_id": None,
            "node_type": "chapter",
            "title": "第一章 勾股定理",
        },
        {
            "id": "section-explore",
            "parent_id": "chapter-pythagoras",
            "node_type": "section",
            "title": "1 探索勾股定理",
        },
        {
            "id": "lesson-understand",
            "parent_id": "section-explore",
            "node_type": "lesson",
            "title": "第1课时 认识勾股定理",
        },
        {
            "id": "lesson-verify",
            "parent_id": "section-explore",
            "node_type": "lesson",
            "title": "第2课时 验证勾股定理",
        },
    ]
    compact = _compact_model_snapshot(snapshot)
    assert compact["available_lessons"] == [
        {
            "id": "lesson-understand",
            "title": "第1课时 认识勾股定理",
            "chapter_title": "第一章 勾股定理",
            "section_title": "1 探索勾股定理",
        },
        {
            "id": "lesson-verify",
            "title": "第2课时 验证勾股定理",
            "chapter_title": "第一章 勾股定理",
            "section_title": "1 探索勾股定理",
        },
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": str(item["title"]),
            "chapter_title": "第一章 勾股定理",
            "section_title": "第一节 探索勾股定理",
            "kind": "section",
        }
        for item in evidence["toc_entries"]
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": annotations,
            "matches": [
                {
                    "lesson_ref": "lesson-understand",
                    "evidence_ids": ["toc-002"],
                    "basis": "同属探索勾股定理",
                },
                {
                    "lesson_ref": "lesson-verify",
                    "evidence_ids": ["toc-002"],
                    "basis": "同属探索勾股定理",
                },
            ],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    assert [item["lesson_ref"] for item in materialized["mappings"]] == [
        "lesson-understand",
        "lesson-verify",
    ]
    assert {
        (item["start_unit"], item["end_unit"])
        for item in materialized["mappings"]
    } == {(9, 14)}


def test_semantic_mapping_completes_partial_existing_lesson_decisions() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-understand",
            "node_type": "lesson",
            "title": "第1课时 认识勾股定理",
        },
        {
            "id": "lesson-verify",
            "node_type": "lesson",
            "title": "第2课时 验证勾股定理",
        },
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": str(item["title"]),
            "chapter_title": "第一章 勾股定理",
            "section_title": "1 探索勾股定理",
            "kind": "section",
        }
        for item in evidence["toc_entries"]
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": annotations,
            "matches": [
                {
                    "lesson_ref": "lesson-understand",
                    "evidence_ids": ["toc-002"],
                    "basis": "同属探索勾股定理",
                }
            ],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    assert [item["lesson_ref"] for item in materialized["mappings"]] == [
        "lesson-understand"
    ]
    assert any(
        "第2课时 验证勾股定理暂未对应" in item
        for item in materialized["uncertainties"]
    )


def test_semantic_mapping_merges_repeated_decisions_for_one_lesson() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-understand",
            "node_type": "lesson",
            "title": "第1课时 认识勾股定理",
        },
        {
            "id": "lesson-verify",
            "node_type": "lesson",
            "title": "第2课时 验证勾股定理",
        },
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": str(item["title"]),
            "chapter_title": "第一章 勾股定理",
            "section_title": "1 探索勾股定理",
            "kind": "section",
        }
        for item in evidence["toc_entries"]
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": annotations,
            "matches": [
                {
                    "lesson_ref": "lesson-understand",
                    "evidence_ids": ["toc-002"],
                    "basis": "同属探索勾股定理",
                },
                {
                    "lesson_ref": "lesson-verify",
                    "evidence_ids": ["toc-002"],
                    "basis": "同属探索勾股定理",
                },
                {
                    "lesson_ref": "lesson-understand",
                    "evidence_ids": ["toc-003"],
                    "basis": "应用小节同属第一课时",
                },
            ],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    assert [item["lesson_ref"] for item in materialized["mappings"]] == [
        "lesson-understand",
        "lesson-understand",
        "lesson-verify",
    ]
    assert [item["start_unit"] for item in materialized["mappings"]] == [
        9,
        15,
        9,
    ]


def test_sparse_workbook_anchors_materialize_existing_lesson_page() -> None:
    snapshot = _snapshot()
    for unit in snapshot["materials"][0]["units"]:
        unit["text_excerpt"] = f"普通练习第 {unit['unit_index']} 页"
        unit["title"] = f"练习 {unit['unit_index']}"
    snapshot["materials"][0]["units"][5].update(
        {
            "title": "第一章 勾股定理",
            "text_excerpt": (
                "第一章 勾股定理\n第1课时 探索勾股定理\n"
                "认识勾股定理基础题"
            ),
        }
    )
    snapshot["lessons"] = [
        {
            "id": "lesson-understand",
            "node_type": "lesson",
            "title": "认识勾股定理",
        }
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    assert evidence["strategy"] == "sparse_outline"
    anchor_ids = {
        item["evidence_id"] for item in evidence["anchors"]
    }
    assert "anchor-0006" in anchor_ids
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": (
                "第1课时 探索勾股定理"
                if item["evidence_id"] == "anchor-0006"
                else str(item.get("title") or "普通练习")
            ),
            "chapter_title": (
                "第一章 勾股定理"
                if item["evidence_id"] == "anchor-0006"
                else ""
            ),
            "section_title": (
                "探索勾股定理"
                if item["evidence_id"] == "anchor-0006"
                else ""
            ),
            "kind": (
                "lesson"
                if item["evidence_id"] == "anchor-0006"
                else "other"
            ),
        }
        for item in evidence["anchors"]
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": annotations,
            "matches": [
                {
                    "lesson_ref": "lesson-understand",
                    "evidence_ids": ["anchor-0006"],
                    "basis": "标题对应第一课时",
                }
            ],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    assert materialized["mappings"][0]["start_unit"] == 6
    assert materialized["mappings"][0]["end_unit"] == 6
    assert materialized["mappings"][0]["evidence_refs"] == [
        "anchor-0006"
    ]


def test_existing_tree_ignores_unresolved_toc_and_uses_page_anchors() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-verify",
            "node_type": "lesson",
            "title": "第2课时 验证勾股定理",
        }
    ]
    snapshot["directory_evidence"] = {
        "strategy": "toc_calibrated",
        "total_unit_count": 67,
        "toc_entries": [
            {
                "evidence_id": "toc-001",
                "title": "D,",
                "source_unit": 18,
                "printed_page": 18,
            }
        ],
        "resolved_ranges": [],
        "anchors": [
            {
                "evidence_id": "anchor-0005",
                "unit_index": 5,
                "title": "素养发展创新练",
                "text_excerpt": "第2课时 勾股定理的验证及其简单应用",
            }
        ],
    }

    compact = _compact_model_snapshot(snapshot)

    assert compact["directory_evidence"]["toc_entries"] == []
    assert compact["directory_evidence"]["anchors"] == [
        snapshot["directory_evidence"]["anchors"][0]
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": [
                {
                    "evidence_id": "anchor-0005",
                    "title": "第2课时 勾股定理的验证及其简单应用",
                    "chapter_title": "第一章 勾股定理",
                    "section_title": "1 探索勾股定理",
                    "kind": "lesson",
                }
            ],
            "matches": [],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    assert materialized["mappings"] == [
        {
            "material_record_id": "record-1",
            "lesson_ref": "lesson-verify",
            "start_unit": 5,
            "end_unit": 5,
            "basis": "本机课时标题匹配",
            "evidence_refs": ["anchor-0005"],
        }
    ]


def test_existing_tree_mapping_tolerates_partial_annotation_metadata() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-existing",
            "node_type": "lesson",
            "title": "探索勾股定理",
        }
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": [
                {
                    "evidence_id": "toc-002",
                    "title": "探索勾股定理",
                    "chapter_title": "",
                    "section_title": "",
                    "kind": "",
                }
            ],
            "matches": [
                {
                    "lesson_ref": "lesson-existing",
                    "evidence_ids": ["toc-002"],
                    "basis": "标题语义一致",
                }
            ],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    assert materialized["mappings"][0]["lesson_ref"] == "lesson-existing"
    assert materialized["mappings"][0]["start_unit"] == 9


def test_existing_tree_mapping_repairs_one_bad_lesson_id_from_unique_title() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-application-complete",
            "node_type": "lesson",
            "title": "第1课时 勾股定理的应用",
        }
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": [
                {
                    "evidence_id": "toc-002",
                    "title": "第1课时 勾股定理的应用",
                    "chapter_title": "第一章 勾股定理",
                    "section_title": "勾股定理的应用",
                    "kind": "lesson",
                }
            ],
            "matches": [
                {
                    "lesson_ref": "lesson-application-incomplete",
                    "evidence_ids": ["toc-002"],
                    "basis": "标题与现有课时完全一致",
                }
            ],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    assert materialized["mappings"][0]["lesson_ref"] == (
        "lesson-application-complete"
    )


def test_existing_tree_mapping_does_not_guess_an_ambiguous_title() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-application-a",
            "node_type": "lesson",
            "title": "第1课时 勾股定理的应用",
        },
        {
            "id": "lesson-application-b",
            "node_type": "lesson",
            "title": "第1课时 勾股定理的应用",
        },
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence

    with pytest.raises(
        TeachingPrepValidationError,
        match="unavailable lesson",
    ):
        materialize_semantic_mapping_payload(
            {
                "annotations": [
                    {
                        "evidence_id": "toc-002",
                        "title": "第1课时 勾股定理的应用",
                        "chapter_title": "第一章 勾股定理",
                        "section_title": "勾股定理的应用",
                        "kind": "lesson",
                    }
                ],
                "matches": [
                    {
                        "lesson_ref": "lesson-application-incomplete",
                        "evidence_ids": ["toc-002"],
                        "basis": "标题存在歧义",
                    }
                ],
                "uncertainties": [],
            },
            snapshot=snapshot,
        )


def _layout_item(
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> dict[str, object]:
    return {
        "text": text,
        "confidence": 0.99,
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
    }


def _chapter_section_skeleton() -> list[dict[str, object]]:
    return [
        {
            "id": "chapter-skeleton",
            "parent_id": None,
            "node_type": "chapter",
            "title": "第一章 勾股定理",
        },
        {
            "id": "section-skeleton",
            "parent_id": "chapter-skeleton",
            "node_type": "section",
            "title": "1 探索勾股定理",
        },
    ]


def test_semantic_mapping_treats_lessonless_skeleton_as_initial_tree() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = _chapter_section_skeleton()
    compact = _compact_model_snapshot(snapshot)
    assert compact["mapping_mode"] == "create_initial_tree"
    assert "available_lessons" not in compact

    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": (
                "第1课时 探索勾股定理"
                if index == 1
                else str(item["title"])
            ),
            "chapter_title": "第一章 勾股定理",
            "section_title": "第一节 探索勾股定理",
            "kind": (
                "chapter" if index == 0 else "lesson" if index == 1 else "special"
            ),
        }
        for index, item in enumerate(evidence["toc_entries"])
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": annotations,
            "matches": [],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )
    result = validate_semester_mapping_payload(
        materialized,
        snapshot=snapshot,
    )

    assert len(result["tree"]) == 1
    assert result["tree"][0]["sections"][0]["lessons"]
    assert result["mappings"]


def test_semantic_mapping_still_rejects_tree_replacement_with_lessons() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        *_chapter_section_skeleton(),
        {
            "id": "lesson-existing",
            "parent_id": "section-skeleton",
            "node_type": "lesson",
            "title": "第1课时 认识勾股定理",
        },
    ]
    snapshot["directory_evidence"] = build_directory_evidence(snapshot)
    raw = {
        "tree": [
            {
                "key": "chapter-a",
                "title": "第一章",
                "sections": [
                    {
                        "key": "section-a",
                        "title": "第一节",
                        "lessons": [
                            {
                                "key": "lesson-a",
                                "title": "第1课时 认识勾股定理",
                                "duration_minutes": 45,
                            }
                        ],
                    }
                ],
            }
        ],
        "mappings": [],
        "uncertainties": [],
    }

    with pytest.raises(
        TeachingPrepValidationError,
        match="replace the existing",
    ):
        validate_semester_mapping_payload(raw, snapshot=snapshot)


def test_textbook_mapping_shares_one_section_page_range_across_lessons() -> None:
    snapshot = _snapshot()
    snapshot["materials"][0]["material_role"] = "textbook"
    snapshot["lessons"] = [
        {
            "id": "chapter-pythagoras",
            "parent_id": None,
            "node_type": "chapter",
            "title": "第一章 勾股定理",
        },
        {
            "id": "section-explore",
            "parent_id": "chapter-pythagoras",
            "node_type": "section",
            "title": "1 探索勾股定理",
        },
        {
            "id": "lesson-understand",
            "parent_id": "section-explore",
            "node_type": "lesson",
            "title": "第1课时 认识勾股定理",
        },
        {
            "id": "lesson-verify",
            "parent_id": "section-explore",
            "node_type": "lesson",
            "title": "第2课时 验证勾股定理",
        },
        {
            "id": "lesson-apply",
            "parent_id": "section-explore",
            "node_type": "lesson",
            "title": "第3课时 应用勾股定理",
        },
    ]
    evidence = build_directory_evidence(snapshot)
    snapshot["directory_evidence"] = evidence
    toc_ids = [str(item["evidence_id"]) for item in evidence["toc_entries"]]
    first_toc, second_toc = toc_ids[1], toc_ids[2]
    first_range = next(
        item
        for item in evidence["resolved_ranges"]
        if item["toc_evidence_id"] == first_toc
    )
    second_range = next(
        item
        for item in evidence["resolved_ranges"]
        if item["toc_evidence_id"] == second_toc
    )
    annotations = [
        {
            "evidence_id": item["evidence_id"],
            "title": str(item["title"]),
            "chapter_title": "第一章 勾股定理",
            "section_title": "1 探索勾股定理",
            "kind": "section",
        }
        for item in evidence["toc_entries"]
    ]

    materialized = materialize_semantic_mapping_payload(
        {
            "annotations": annotations,
            "matches": [
                {
                    "lesson_ref": "lesson-understand",
                    "evidence_ids": [first_toc],
                    "basis": "同属探索勾股定理",
                },
                {
                    "lesson_ref": "lesson-verify",
                    "evidence_ids": [second_toc],
                    "basis": "同属探索勾股定理",
                },
            ],
            "uncertainties": [],
        },
        snapshot=snapshot,
    )

    shared = (
        min(int(first_range["start_unit"]), int(second_range["start_unit"])),
        max(int(first_range["end_unit"]), int(second_range["end_unit"])),
    )
    by_lesson = {
        str(item["lesson_ref"]): (int(item["start_unit"]), int(item["end_unit"]))
        for item in materialized["mappings"]
    }
    assert by_lesson == {
        "lesson-understand": shared,
        "lesson-verify": shared,
        "lesson-apply": shared,
    }
    assert not any(
        "第3课时 应用勾股定理暂未对应" in item
        for item in materialized["uncertainties"]
    )


def test_textbook_offset_falls_back_when_median_collapses_front_pages() -> None:
    entries = [
        {"printed_page": page}
        for page in (2, 10, 13, 16, 19, 25, 40, 80, 120, 180)
    ]
    offset, issues, agreed = _choose_printed_offset(
        [8, 8, -82, -82, -82, -82],
        entries=entries,
        total_units=212,
        material_role="textbook",
    )
    assert offset == 0
    assert agreed is False
    assert any("偏差过大" in item for item in issues)


def test_workbook_offset_keeps_median_even_when_spread_is_wide() -> None:
    entries = [{"printed_page": page} for page in (2, 10, 13, 16, 19)]
    offset, issues, agreed = _choose_printed_offset(
        [8, 8, -82, -82, -82],
        entries=entries,
        total_units=144,
        material_role="exercise_workbook",
    )
    assert offset == -82
    assert agreed is False
    assert issues
    assert not any("偏差过大" in item for item in issues)


def test_consistent_wrong_offset_falls_back_when_it_collapses_front() -> None:
    entries = [
        {"printed_page": page}
        for page in (1, 2, 8, 75, 79, 105, 171)
    ]
    offset, issues, agreed = _choose_printed_offset(
        [-82] * 6,
        entries=entries,
        total_units=212,
        material_role="textbook",
    )
    assert offset == 0
    assert agreed is False
    assert any("偏差过大" in item for item in issues)


def test_select_printed_offset_prefers_visible_page_footer() -> None:
    entries = [{"printed_page": page, "title": "x"} for page in (2, 10, 79, 89, 171)]
    offset, issues, agreed, used = _select_printed_offset(
        visible_offsets=[10] * 20,
        title_offsets=[-82, -82, -82],
        entries=entries,
        total_units=212,
        material_role="textbook",
    )
    assert offset == 10
    assert used == [10] * 20
    assert agreed is True
    assert any("页脚" in item for item in issues)


def test_repair_recovers_page_number_stuck_in_title() -> None:
    entries = [
        {
            "title": "3 哪个团队收益大 /115",
            "printed_page": 0,
            "page_refs": {},
        }
    ]
    _repair_toc_printed_pages(entries, total_units=212)
    assert entries[0]["printed_page"] == 115
    assert entries[0]["title"] == "3 哪个团队收益大"


def test_heading_level_treats_numbered_section_titles_as_section() -> None:
    assert _heading_level("第一章 一次函数") == "chapter"
    assert _heading_level("3 哪个团队收益大") == "section"
    assert _heading_level("1.1 探索勾股定理") == "section"


def test_rematerialize_rebuilds_page_ranges_from_new_local_evidence() -> None:
    snapshot = _snapshot()
    snapshot["lessons"] = [
        {
            "id": "lesson-understand",
            "parent_id": None,
            "node_type": "lesson",
            "title": "1.1 探索勾股定理",
            "sort_order": 1,
        }
    ]
    snapshot["materials"][0]["material_role"] = "textbook"
    evidence = build_directory_evidence(snapshot)
    toc_id = next(
        str(item["evidence_id"])
        for item in evidence["toc_entries"]
        if "探索勾股定理" in str(item["title"])
        and "1.1" in str(item["title"])
    )
    expected = next(
        item
        for item in evidence["resolved_ranges"]
        if item["toc_evidence_id"] == toc_id
    )
    rebuilt = rematerialize_existing_mapping_payload(
        {
            "tree": [],
            "mappings": [
                {
                    "lesson_ref": "lesson-understand",
                    "evidence_refs": [toc_id],
                    "start_unit": 89,
                    "end_unit": 91,
                    "material_record_id": "record-1",
                }
            ],
            "uncertainties": [],
            "source_material_record_ids": ["record-1"],
            "directory_evidence": {
                "toc_entries": [
                    {
                        "evidence_id": toc_id,
                        "title": "1.1 探索勾股定理",
                        "printed_page": 3,
                        "source_unit": 2,
                        "level": "section",
                    }
                ]
            },
        },
        snapshot=snapshot,
    )
    mapping = rebuilt["mappings"][0]
    assert mapping["start_unit"] == expected["start_unit"]
    assert mapping["end_unit"] == expected["end_unit"]
    assert mapping["start_unit"] != 89
    assert "未调用模型" in str(rebuilt["uncertainties"][0])


def test_rematerialize_rejects_local_ppt_mappings() -> None:
    with pytest.raises(TeachingPrepValidationError):
        rematerialize_existing_mapping_payload(
            {"generation_source": "local_reference_ppt_names"},
            snapshot=_snapshot(),
        )
