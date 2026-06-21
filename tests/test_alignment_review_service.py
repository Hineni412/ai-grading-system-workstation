from question_bank.services.alignment_review_service import (
    AlignmentFocusItem,
    filter_focus_sources,
    focus_items_from_diagnosis,
    merge_focus_sources,
)


def _diagnosis() -> dict:
    return {
        "exam_scope": {"session_ids": [1]},
        "students": [
            {
                "student_id": "70",
                "weak_points": [
                    {
                        "source_term": "角平分线性质",
                        "source_display": "G7_15 · 角平分线性质",
                        "mapping_status": "suggested",
                        "evidence_count": 3,
                    }
                ],
            }
        ],
    }


def test_focus_item_preserves_namespace_and_provenance() -> None:
    items = focus_items_from_diagnosis(_diagnosis())

    assert items == [
        AlignmentFocusItem(
            source_namespace="grading_weak_point",
            source_value="角平分线性质",
            display_value="G7_15 · 角平分线性质",
            evidence_count=3,
            student_ids=("70",),
            session_ids=(1,),
        )
    ]


def test_missing_focus_source_is_injected_and_same_name_question_tag_is_not_used() -> None:
    focus = focus_items_from_diagnosis(_diagnosis())
    sources = [
        {
            "source_namespace": "question_tag",
            "source_value": "角平分线性质",
            "display_value": "角平分线性质",
            "evidence_count": 4,
        }
    ]

    merged = merge_focus_sources(sources, focus)
    filtered = filter_focus_sources(merged, focus)

    assert len(filtered) == 1
    assert filtered[0]["source_namespace"] == "grading_weak_point"
    assert filtered[0]["source_value"] == "角平分线性质"
    assert filtered[0]["evidence_count"] == 3
