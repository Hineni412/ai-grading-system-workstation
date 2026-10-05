from pathlib import Path

from backend.scan_grading.grading_run_identity import (
    CandidatePaper,
    CompletedIdentity,
    classify_student_candidates,
    grading_config_fingerprint,
    paper_fingerprint,
)


def test_same_student_same_paper_and_config_is_skipped(tmp_path: Path) -> None:
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    front.write_bytes(b"front")
    back.write_bytes(b"back")
    paper = paper_fingerprint(front, back)
    candidates = [CandidatePaper("a", 7, paper), CandidatePaper("b", 7, paper)]
    completed = [CompletedIdentity(7, paper, "cfg", True)]

    decisions = classify_student_candidates(candidates, completed, "cfg")

    assert [item.action for item in decisions] == [
        "skipped_existing",
        "skipped_duplicate",
    ]


def test_same_student_different_papers_are_all_conflicts() -> None:
    candidates = [
        CandidatePaper("a", 7, "paper-a"),
        CandidatePaper("b", 7, "paper-b"),
    ]

    decisions = classify_student_candidates(candidates, [], "cfg")

    assert {item.action for item in decisions} == {"conflict"}
