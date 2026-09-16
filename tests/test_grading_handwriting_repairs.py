"""Synthetic regressions for final-answer recognition and partial retry evidence."""
from __future__ import annotations

import pytest

from answer_normalizer import match_fill_blank_answer
from choice_recognition_chain import score_choice_by_program
from agent_bridge.respond_obj import build_response, _parse
from agent_bridge.respond_subj import _detail
from grading_completeness import merge_detail_metadata, details_require_review
from objective_batch_recognition_service import ObjectiveQuestionSpec, validate_objective_paper_response
from backend.review.service import _is_substantive_review_reason, _detail_metadata_for_qid


@pytest.mark.parametrize("answer,standard,matched", [
    ("-1+√7", "√7-1", True), ("√28/2-1", "√7-1", True),
    (r"\sqrt{7}-1", "√7-1", True), ("√7+1", "√7-1", False),
    ("7-1", "√7-1", False), ("2^3", "8", True),
    ("1/2", "0.5", True), ("3/4", "3/5", False),
    ("＞", ">", True), (">", "≥", False), (">=", "≥", True),
    ("1+2", "3", True), ("B.2", "2", None), ("2~82", "2", None),
    ("√", "√7-1", None), ("1/0", "1", None),
])
def test_complete_math_expression(answer, standard, matched):
    assert match_fill_blank_answer(answer, standard)["matched"] is matched


@pytest.mark.parametrize("answer", ["B~82", "CB.2", "B2", "AB", "[B]extra"])
def test_choice_contamination_never_becomes_confident_wrong_score(answer):
    result = score_choice_by_program(answer, "B", 6, 0.99)
    assert result["need_review"] is True
    assert result["review_reason"] == "invalid_choice_answer"


def test_bridge_compatibility_is_explicit_and_confidence_is_not_invented():
    assert _parse("B~82") == ("B", .82, False, "changed_answer_clear_replacement")
    assert _parse("B")[1:3] == (0.0, True)
    assert _parse("discarded")[1:3] == (0.0, True)
    assert _parse("conf=95:discarded") == ("", .95, False, "discarded_answer_only")
    manifest = {"paper_key": "exact_identity", "student_id": 41,
                "target_question_ids": ["Q1"], "question_types": {"Q1": "choice"}}
    result = build_response(manifest, [{"recognized_answer": "B", "confidence": .93,
        "answer_state": "clear", "has_discarded_content": True}])
    assert result["paper_key"] == "exact_identity"
    assert result["student_id"] == 41
    assert result["answers"][0]["confidence"] == .93
    for value in ({"recognized_answer": "B"}, {"recognized_answer": "B.2", "confidence": .99}):
        with pytest.raises(ValueError):
            build_response(manifest, [value])


@pytest.mark.parametrize("state,answer,confidence,flag,expected_review", [
    ("clear", "B", .95, False, False),
    ("discarded_only", "B", .95, False, True),
    ("discarded_only", "", .95, False, False),
    ("discarded_only", "", .5, False, True),
    ("uncertain", "B", .95, False, True),
    ("clear", "B", .95, True, True),
])
def test_valid_replacement_and_discard_are_distinct(state, answer, confidence, flag, expected_review):
    spec = ObjectiveQuestionSpec("Q1", "choice", "B", 6, {})
    accepted, review = validate_objective_paper_response(
        response={"paper_key": "p", "answers": [{"question_id": "Q1",
            "recognized_answer": answer, "confidence": confidence, "need_review": flag,
            "answer_state": state, "has_discarded_content": True,
            "score_awarded": 6 if answer else 0, "deduction_reason": "仅有作废作答。" if not answer else "",
            "review_reason": "clear_replacement_after_discard"}]},
        manifest={"paper_key": "p", "student_id": 1, "target_question_ids": ["Q1"]},
        specs=[spec], min_confidence=.8)
    assert not review
    assert accepted[0]["metadata"]["need_review"] is expected_review
    if not expected_review:
        assert accepted[0]["detail"].score_awarded == (6 if answer else 0)


def test_retry_evidence_merge_drops_replaced_evidence_and_preserves_other_questions():
    old = {"detail_metadata": {"Q1": {"observed_answer": "B"}, "Q2": {"need_review": True}, "Q3": {"old": True}}}
    merged = merge_detail_metadata(old, {"detail_metadata": {"Q2": {"need_review": False}}}, ["Q2", "Q3"])
    assert merged["detail_metadata"] == {"Q1": {"observed_answer": "B"}, "Q2": {"need_review": False}}
    assert old["detail_metadata"]["Q2"]["need_review"] is True
    assert "Q3" not in merge_detail_metadata(old, {}, ["Q3"])["detail_metadata"]


def test_explicit_review_survives_high_confidence_and_non_review_error_category():
    raw = {"detail_metadata": {"Q1": {"needs_human_review": True}}}
    detail = {"question_id": "Q1", "confidence_score": 95, "error_category": "其他"}
    assert details_require_review([detail], raw)
    metadata = _detail_metadata_for_qid(raw, "Q1")
    assert _is_substantive_review_reason("", "其他", 95, explicit_review=metadata["needs_human_review"])
    detail["error_category"] = "教师已确认"
    assert not details_require_review([detail], raw)
    assert not _is_substantive_review_reason("", "教师已确认", 95, explicit_review=True)


def test_subjective_bridge_requires_real_evidence_and_confidence():
    spec = {"steps": {"S1": [3, "full", "符合关系"]}, "conf": 95}
    with pytest.raises(ValueError, match="evidence"):
        _detail("Q1", spec)
    spec["steps"]["S1"].append("3²+4²=5²")
    assert _detail("Q1", spec)["step_assessments"][0]["student_evidence"] == "3²+4²=5²"
    del spec["conf"]
    with pytest.raises(ValueError, match="confidence"):
        _detail("Q1", spec)
