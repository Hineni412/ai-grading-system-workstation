from pathlib import Path

from grading_run_identity import (
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


def test_changed_config_allows_regrading() -> None:
    candidate = CandidatePaper("a", 7, "paper-a")
    completed = [CompletedIdentity(7, "paper-a", "old", True)]

    assert classify_student_candidates([candidate], completed, "new")[0].action == "grade"


def test_incomplete_prior_result_does_not_skip() -> None:
    candidate = CandidatePaper("a", 7, "paper-a")
    completed = [CompletedIdentity(7, "paper-a", "cfg", False)]

    assert classify_student_candidates([candidate], completed, "cfg")[0].action == "grade"


def test_paper_fingerprint_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    front = tmp_path / "f.png"
    back = tmp_path / "b.png"
    front.write_bytes(b"aa")
    back.write_bytes(b"bb")
    fp1 = paper_fingerprint(front, back)
    fp2 = paper_fingerprint(front, back)
    back.write_bytes(b"bc")
    fp3 = paper_fingerprint(front, back)

    assert fp1 == fp2
    assert fp1 != fp3


def test_config_fingerprint_ignores_runtime_params_but_tracks_rubric() -> None:
    rubric_a = {"questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]}
    answer = {"questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]}
    regions = [{"page": "front", "region_order": 1, "mapped_question_id": "Q1"}]

    base = grading_config_fingerprint(
        rubric=rubric_a, answer_key=answer, answer_regions=regions,
        grading_mode="full_paper", grading_model="m",
    )
    same_ids_legacy_form = grading_config_fingerprint(
        rubric={"questions": [{"question_id": "Q1", "parts": [{"part_id": "Q1"}]}]},
        answer_key=answer, answer_regions=regions,
        grading_mode="full_paper", grading_model="m",
    )
    changed_mode = grading_config_fingerprint(
        rubric=rubric_a, answer_key=answer, answer_regions=regions,
        grading_mode="hybrid", grading_model="m",
    )

    # 单小问的两种写法规范化后一致，指纹相同
    assert base == same_ids_legacy_form
    # 批改模式改变则指纹改变
    assert base != changed_mode
