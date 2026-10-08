"""Regression cases from the dedup/flexible-grading acceptance review."""

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.domain_models import GradingResult
from backend.repositories.results import ResultRepository
from backend.scan_grading.ai_batch_grading_service import _detail_from_ai_item, build_major_question_specs
from backend.repositories.grading_database import open_grading_repositories


def question_spec():
    question = {
        "question_id": "Q1",
        "question_type": "calculation",
        "max_score": 9,
        "answer_only_max_score": 1,
        "parts": [
            {
                "part_id": "Q1",
                "part_score": 9,
                "response_mode": "process_required",
                "answer_only_max_score": 1,
                "steps": [
                    {"step_id": f"S{i + 1}", "step_score": 3, "core_goal": goal}
                    for i, goal in enumerate(("建立和关系", "建立积关系", "求解"))
                ],
            }
        ],
    }
    return build_major_question_specs(
        {"total_score": 9, "questions": [question]},
        {"questions": []},
    )[0]


def assessments(achievements=("full", "full", "none")):
    evidences = ("a+b=10", "ab=24", "a=4,b=6")
    return [
        {
            "step_id": f"S{i + 1}",
            "achievement": achievement,
            "score_awarded": 3 if achievement in {"full", "equivalent"} else 0,
            "student_evidence": evidences[i] if achievement != "none" else "",
            "missing_or_error": ""
            if achievement in {"full", "equivalent"}
            else "局部缺漏",
            "reason": "本块有效成果及缺漏",
        }
        for i, achievement in enumerate(achievements)
    ]


def convert(item):
    spec = question_spec()
    detail, reason, metadata = _detail_from_ai_item(
        copy.deepcopy(item), set(spec.detail_question_ids), 80, spec=spec
    )
    assert not reason
    return detail, metadata


@pytest.mark.parametrize(
    "required,equivalent,simplified,expected",
    [
        (True, True, False, 8),
        (False, True, False, 9),
        (True, True, True, 9),
        (True, False, False, 9),
    ],
)
def test_final_simplification_deducts_exactly_one(
    required, equivalent, simplified, expected
):
    item = {
        "question_id": "Q1",
        "score_awarded": 9,
        "deduction_reason": "",
        "step_assessments": assessments(("full", "full", "full")),
        "final_answer_simplification": {
            "required": required,
            "equivalent": equivalent,
            "simplified": simplified,
            "student_evidence": "(2√3)/2−1",
            "requirement_evidence": "计算最简结果",
        },
    }
    detail, metadata = convert(item)
    assert detail.score_awarded == expected
    assert sum(step["score_awarded"] for step in metadata["step_assessments"]) == 9
    if expected == 8:
        assert metadata["presentation_deduction"] == 1
        assert "未完成化简" in detail.deduction_reason


@pytest.mark.parametrize("observed", ["4和6", "a=4，b=6"])
@pytest.mark.parametrize("raw_score", [0, 1])
def test_correct_answer_without_work_is_one(observed, raw_score):
    item = {
        "question_id": "Q1",
        "score_awarded": raw_score,
        "deduction_reason": "只有正确答案",
        "observed_answer": observed,
        "answer_only_correct": True,
        "answer_is_blank_or_no_valid_work": True,
        "step_assessments": assessments(("none", "none", "none")),
    }
    detail, metadata = convert(item)
    assert detail.score_awarded == 1
    assert all(s["achievement"] == "none" for s in metadata["step_assessments"])
    assert sum(s["score_awarded"] for s in metadata["step_assessments"]) == 0


def test_repository_rejects_fractional_score_before_replacing_existing_result():
    detail, metadata = convert(
        {
            "question_id": "Q1",
            "score_awarded": 6,
            "deduction_reason": "",
            "step_assessments": assessments(),
        }
    )
    result = GradingResult(
        student_name="合成验收",
        total_score=9,
        student_score=detail.score_awarded,
        needs_human_review=False,
        grading_details=[detail],
        raw_json={"detail_metadata": {"Q1": metadata}},
    )
    invalid = replace(
        result, grading_details=[replace(result.grading_details[0], score_awarded=8.5)]
    )
    with pytest.raises(ValueError, match="score_contract_error"):
        ResultRepository(SimpleNamespace()).save_session_result(1, 1, 1, invalid)
    with pytest.raises(ValueError, match="score_contract_error"):
        ResultRepository(SimpleNamespace()).replace_result_details_atomic(
            1,
            ["Q1"],
            invalid.grading_details,
            needs_human_review=True,
            raw_json=invalid.raw_json,
        )


def test_saving_new_ai_grade_keeps_historical_fractional_teacher_lock_and_evidence(
    tmp_path,
):
    import json
    import sqlite3
    from backend.domain_models import GradingResult, QuestionGradingDetail
    
    from tests.test_report_ai_teacher_comparison import _seed_mixed_session

    db_path = _seed_mixed_session(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE teacher_score_locks SET score_awarded=4.5 WHERE question_id='Q1'"
        )
    steps = [
        {
            "step_id": "S1",
            "achievement": "partial",
            "score_awarded": 4,
            "student_evidence": "a+b=10",
            "missing_or_error": "缺结论",
            "reason": "关系成立，结论未完成",
        }
    ]
    result = GradingResult(
        student_name="合成验收",
        total_score=10,
        student_score=7,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail("Q1", 4, "缺结论", "K1"),
            QuestionGradingDetail("Q2", 3, "局部缺漏", "K2"),
        ],
        raw_json={"detail_metadata": {"Q1": {"step_assessments": steps}}},
    )
    db = open_grading_repositories(db_path)
    saved_id = db.results.save_session_result(
        1, 1, 1, result, scan_batch_id="batch-1"
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT score_awarded,ai_score_awarded FROM session_details WHERE result_id=? AND question_id='Q1'",
            (saved_id,),
        ).fetchone() == (4.5, 4)
        saved = conn.execute(
            "SELECT student_score,raw_json FROM session_results WHERE id=?", (saved_id,)
        ).fetchone()
        assert saved[0] == 7.5
        assert (
            json.loads(saved[1])["detail_metadata"]["Q1"]["step_assessments"] == steps
        )
    invalid = replace(
        result, grading_details=[replace(result.grading_details[0], score_awarded=2.5)]
    )
    with pytest.raises(ValueError, match="score_contract_error"):
        db.results.save_session_result(
            1, 1, 1, invalid, scan_batch_id="batch-1"
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT id,student_score FROM session_results"
        ).fetchone() == (saved_id, 7.5)


def test_mixed_open_result_and_proof_prompt_retains_separate_obligations() -> None:
    import json
    from backend.scan_grading.ai_batch_grading_service import MajorQuestionSpec, build_ai_major_prompt

    parts = [
        {"part_id": "Q1(P1)", "part_score": 2, "response_mode": "exact_objective", "steps": [
            {"step_id": "S1", "step_score": 2, "answer_kind": "conditions", "core_goal": "给出一个大于 2 的整数", "required_elements": ["答案是整数且大于 2"]}]},
        {"part_id": "Q1(P2)", "part_score": 6, "response_mode": "process_required", "steps": [
            {"step_id": "S1", "step_score": 3, "core_goal": "建立全等条件", "required_elements": ["三组对应关系"]},
            {"step_id": "S2", "step_score": 3, "core_goal": "推出对应边相等", "required_elements": ["全等结论和对应边相等"]}]},
    ]
    spec = MajorQuestionSpec("Q1", ["Q1(P1)", "Q1(P2)"], {"question_id": "Q1", "question_type": "comprehensive", "max_score": 8, "parts": parts},
        {"question_id": "Q1", "parts": [{"part_id": "Q1(P1)", "answer": "3"}, {"part_id": "Q1(P2)", "answer": "证明略"}]}, 8)
    system, static, _dynamic = build_ai_major_prompt(spec, {"items": []})
    payload = json.loads(static.split("QUESTION_PAYLOAD_JSON:", 1)[1])
    actual = payload["rubric"]["parts"]
    assert actual[0]["steps"][0]["answer_kind"] == "conditions"
    assert actual[0]["steps"][0]["required_elements"] == ["答案是整数且大于 2"]
    assert actual[1]["response_mode"] == "process_required"
    assert all("answer_kind" not in step for step in actual[1]["steps"])
    assert "不同空按各点 answer_kind 独立判断" in system
    assert "只有 response_mode=process_required 的小问需要证明或推理证据" in system
    assert "旧资料未提供 answer_kind 时继续依据完整题意" in system
