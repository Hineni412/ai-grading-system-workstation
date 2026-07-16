from pathlib import Path

from grading_service import apply_scan_manual_decisions
from scanner import ExamPaperGroup, ScanAnalysis


def test_group_reassignment_is_applied_before_grading() -> None:
    analysis = ScanAnalysis(
        groups=[
            ExamPaperGroup(
                front_image=Path("front.jpg"),
                back_image=Path("back.jpg"),
                student_name="Original student",
                student_id=11,
                source_label="paper-001",
                match_method="fuzzy",
                match_score=0.72,
            )
        ]
    )

    groups = apply_scan_manual_decisions(
        analysis,
        [
            {
                "group_source_label": "paper-001",
                "action": "match",
                "student_id": 12,
            }
        ],
        [{"id": 11, "name": "Original student"}, {"id": 12, "name": "Correct student"}],
    )

    assert len(groups) == 1
    assert groups[0].student_id == 12
    assert groups[0].student_name == "Correct student"
    assert groups[0].match_method == "manual"
    assert groups[0].match_score == 1.0
