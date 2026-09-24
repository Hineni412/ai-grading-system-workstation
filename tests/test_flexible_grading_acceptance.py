"""Regression cases from the dedup/flexible-grading acceptance review."""
import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ai_grader import AIGrader
from backend.repositories.results import ResultRepository
from hybrid_batch_grading_service import MajorQuestionSpec, _detail_from_ai_item
from solution_answer_guard import validate_step_assessments


def grader_and_spec():
    question = {"question_id": "Q1", "question_type": "calculation", "max_score": 9,
                "answer_only_max_score": 1, "parts": [{"part_id": "Q1", "part_score": 9,
                "response_mode": "process_required", "answer_only_max_score": 1,
                "steps": [{"step_id": f"S{i+1}", "step_score": 3,
                           "core_goal": goal} for i, goal in enumerate(("建立和关系", "建立积关系", "求解"))]}]}
    grader = AIGrader.__new__(AIGrader)
    grader.rubric = {"total_score": 9, "questions": [question]}
    grader.answer_key = {}; grader.question_tag_context = {}; grader.target_question_ids = []
    return grader, MajorQuestionSpec(question_id="Q1", rubric=question, answer_key={},
                                    max_score=9, detail_question_ids=["Q1"])


def assessments(achievements=("full", "full", "none")):
    evidences = ("a+b=10", "ab=24", "a=4,b=6")
    return [{"step_id": f"S{i+1}", "achievement": achievement,
             "score_awarded": 3 if achievement in {"full", "equivalent"} else 0,
             "student_evidence": evidences[i] if achievement != "none" else "",
             "missing_or_error": "" if achievement in {"full", "equivalent"} else "局部缺漏",
             "reason": "本块有效成果及缺漏"}
            for i, achievement in enumerate(achievements)]


def convert(grader, item):
    return grader._validate_and_convert({"student_name": "合成验收", "total_score": 9,
        "student_score": item["score_awarded"], "needs_human_review": False,
        "grading_details": [copy.deepcopy(item)]}, expected_student_name="合成验收")


@pytest.mark.parametrize("required,equivalent,simplified,expected", [
    (True, True, False, 8), (False, True, False, 9),
    (True, True, True, 9), (True, False, False, 9),
])
def test_final_simplification_deducts_exactly_one_in_both_grading_modes(required, equivalent, simplified, expected):
    grader, spec = grader_and_spec()
    item = {'question_id': 'Q1', 'score_awarded': 9, 'deduction_reason': '',
            'step_assessments': assessments(('full', 'full', 'full')),
            'final_answer_simplification': {'required': required, 'equivalent': equivalent,
                'simplified': simplified, 'student_evidence': '(2√3)/2−1', 'requirement_evidence': '计算最简结果'}}
    result = convert(grader, item)
    detail, reason, metadata = _detail_from_ai_item(copy.deepcopy(item), {'Q1'}, 80, spec=spec)
    assert not reason
    assert result.student_score == detail.score_awarded == expected
    assert sum(step['score_awarded'] for step in metadata['step_assessments']) == 9
    if expected == 8:
        assert metadata['presentation_deduction'] == 1
        assert '未完成化简' in detail.deduction_reason


@pytest.mark.parametrize("observed", ["4和6", "a=4，b=6"])
@pytest.mark.parametrize("raw_score", [0, 1])
def test_correct_answer_without_work_is_one_in_both_modes(observed, raw_score):
    grader, spec = grader_and_spec()
    item = {"question_id": "Q1", "score_awarded": raw_score, "deduction_reason": "只有正确答案",
            "observed_answer": observed, "answer_only_correct": True,
            "answer_is_blank_or_no_valid_work": True,
            "step_assessments": assessments(("none", "none", "none"))}
    full = convert(grader, item)
    hybrid, reason, metadata = _detail_from_ai_item(copy.deepcopy(item), {"Q1"}, 80, spec=spec)
    assert full.student_score == 1
    assert not reason and hybrid.score_awarded == 1
    assert all(s["achievement"] == "none" for s in metadata["step_assessments"])
    assert sum(s["score_awarded"] for s in metadata["step_assessments"]) == 0


def test_step_evidence_alone_preserves_unit_score_in_both_modes():
    grader, spec = grader_and_spec()
    item = {"question_id": "Q1", "score_awarded": 6, "deduction_reason": "求解步骤未达成",
            "step_assessments": assessments()}
    assert convert(grader, item).student_score == 6
    detail, reason, _ = _detail_from_ai_item(copy.deepcopy(item), {"Q1"}, 80, spec=spec)
    assert not reason and detail.score_awarded == 6


@pytest.mark.parametrize("score", [8.5, True, False, float("nan"), float("inf"), -1, 10])
def test_invalid_new_scores_are_rejected_before_conversion(score):
    grader, spec = grader_and_spec()
    item = {"question_id": "Q1", "score_awarded": score, "deduction_reason": "", "step_assessments": assessments()}
    with pytest.raises(ValueError, match="score_contract_error"):
        convert(grader, item)
    detail, reason, _ = _detail_from_ai_item(copy.deepcopy(item), {"Q1"}, 80, spec=spec)
    assert detail is None and reason.startswith("score_contract_error")


@pytest.mark.parametrize("kind", ["missing", "none_full", "empty_evidence", "retired_partial", "none_no_defect", "wrong_part"])
def test_invalid_step_contract_cannot_be_saved(kind):
    grader, spec = grader_and_spec()
    steps = assessments()
    if kind == "missing": steps = None
    elif kind == "none_full": steps[0]["achievement"] = "none"
    elif kind == "empty_evidence": steps[0]["student_evidence"] = ""
    elif kind == "retired_partial": steps[0]["achievement"] = "partial"
    elif kind == "none_no_defect": steps[2]["missing_or_error"] = ""
    else:
        steps[0]["part_id"] = "Q2"
    item = {"question_id": "Q1", "score_awarded": 6, "deduction_reason": "",
            "observed_answer": "a+b=10，ab=24，a=4,b=6", "step_assessments": steps}
    full = convert(grader, item)
    assert full.needs_human_review
    # No connection exists: rejection must precede every database mutation.
    with pytest.raises(ValueError, match="score_contract_error"):
        ResultRepository(SimpleNamespace()).save_session_result(1, 1, 1, full)
    with pytest.raises(ValueError, match="score_contract_error"):
        ResultRepository(SimpleNamespace()).replace_result_details_atomic(
            1, ["Q1"], full.grading_details, needs_human_review=True, raw_json=full.raw_json)
    detail, reason, _ = _detail_from_ai_item(copy.deepcopy(item), {"Q1"}, 80, spec=spec)
    assert detail is None and reason.startswith("score_contract_error")


def test_repository_rejects_fractional_score_before_replacing_existing_result():
    grader, _ = grader_and_spec()
    result = convert(grader, {"question_id": "Q1", "score_awarded": 6,
                              "deduction_reason": "", "step_assessments": assessments()})
    invalid = replace(result, grading_details=[replace(result.grading_details[0], score_awarded=8.5)])
    with pytest.raises(ValueError, match="score_contract_error"):
        ResultRepository(SimpleNamespace()).save_session_result(1, 1, 1, invalid)
    with pytest.raises(ValueError, match="score_contract_error"):
        ResultRepository(SimpleNamespace()).replace_result_details_atomic(
            1, ["Q1"], invalid.grading_details, needs_human_review=True, raw_json=invalid.raw_json)


def test_repeated_step_ids_in_parent_require_part_identity():
    grader, _ = grader_and_spec()
    parts = grader.rubric["questions"][0]["parts"]
    parts[0]["part_id"] = "Q1(P1)"
    parts.append(copy.deepcopy(parts[0])); parts[1]["part_id"] = "Q1(P2)"
    steps = [{**item, "part_id": part["part_id"]} for part in parts for item in assessments()]
    normalized, error = validate_step_assessments(steps, rubric=grader.rubric, question_id="Q1", score_awarded=12)
    assert error is None and len(normalized) == 6
    del steps[3]["part_id"]
    assert validate_step_assessments(steps, rubric=grader.rubric, question_id="Q1", score_awarded=12)[1]


def test_saving_new_ai_grade_keeps_historical_fractional_teacher_lock_and_evidence(tmp_path):
    import json
    import sqlite3
    from backend.domain_models import GradingResult, QuestionGradingDetail
    from db_manager import DBManager
    from tests.test_report_ai_teacher_comparison import _seed_mixed_session
    db_path = _seed_mixed_session(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE teacher_score_locks SET score_awarded=4.5 WHERE question_id='Q1'")
    steps = [{"step_id": "S1", "achievement": "partial", "score_awarded": 4,
              "student_evidence": "a+b=10", "missing_or_error": "缺结论", "reason": "关系成立，结论未完成"}]
    result = GradingResult(student_name="合成验收", total_score=10, student_score=7,
        needs_human_review=False, grading_details=[QuestionGradingDetail("Q1",4,"缺结论","K1"),
        QuestionGradingDetail("Q2",3,"局部缺漏","K2")],
        raw_json={"detail_metadata":{"Q1":{"step_assessments":steps}}})
    db = DBManager(db_path)
    saved_id = db.result_repository.save_session_result(1,1,1,result,scan_batch_id="batch-1")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT score_awarded,ai_score_awarded FROM session_details WHERE result_id=? AND question_id='Q1'", (saved_id,)).fetchone() == (4.5,4)
        saved = conn.execute("SELECT student_score,raw_json FROM session_results WHERE id=?",(saved_id,)).fetchone()
        assert saved[0] == 7.5
        assert json.loads(saved[1])["detail_metadata"]["Q1"]["step_assessments"] == steps
    invalid = replace(result, grading_details=[replace(result.grading_details[0],score_awarded=2.5)])
    with pytest.raises(ValueError, match="score_contract_error"):
        db.result_repository.save_session_result(1,1,1,invalid,scan_batch_id="batch-1")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT id,student_score FROM session_results").fetchone() == (saved_id,7.5)
