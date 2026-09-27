import json
import tempfile
import unittest
from pathlib import Path

from ai_grader import AIGrader
from solution_answer_guard import (
    uncertain_step_ids,
    validate_step_assessments,
)


class _FakeLLMClient:
    pass


class SolutionAnswerGuardTests(unittest.TestCase):
    def test_subjective_stem_echo_gets_zero_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rubric_path = root / "rubric.json"
            rubric_path.write_text(
                json.dumps(
                    {
                        "total_score": 4,
                        "questions": [
                            {
                                "question_id": "Q12",
                                "question_type": "proof",
                                "max_score": 12,
                                "answer_only_max_score": 1,
                                "parts": [{"part_id": "Q12", "part_score": 4}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            grader = AIGrader(rubric_path, _FakeLLMClient())
            result = grader._validate_and_convert(
                {
                    "student_name": "余天策",
                    "total_score": 4,
                    "student_score": 4,
                    "needs_human_review": False,
                    "grading_details": [
                        {
                            "question_id": "Q12",
                            "observed_answer": "(3)是不是定值√",
                            "score_awarded": 4,
                            "deduction_reason": "",
                            "knowledge_id": "K1",
                            "knowledge_ids": ["K1"],
                        }
                    ],
                },
                expected_student_name="余天策",
            )

            self.assertEqual(result.student_score, 0)
            self.assertEqual(result.grading_details[0].score_awarded, 0)
            self.assertEqual(result.grading_details[0].error_category, "未作答")

    def _grader_with_three_step_question(self, root: Path):
        rubric_path = root / "rubric.json"
        rubric_path.write_text(
            json.dumps(
                {
                    "total_score": 9,
                    "questions": [
                        {
                            "question_id": "Q1",
                            "question_type": "calculation",
                            "max_score": 9,
                            "answer_only_max_score": 1,
                            "parts": [
                                {
                                    "part_id": "Q1",
                                    "part_score": 9,
                                    "response_mode": "process_required",
                                    "steps": [
                                        {
                                            "step_id": "S1",
                                            "step_score": 3,
                                            "core_goal": "建立边长和关系",
                                        },
                                        {
                                            "step_id": "S2",
                                            "step_score": 3,
                                            "core_goal": "建立面积关系并联立",
                                        },
                                        {
                                            "step_id": "S3",
                                            "step_score": 3,
                                            "core_goal": "求解并回答边长",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return AIGrader(rubric_path, _FakeLLMClient())

    def _process_detail(self, score, assessments=None, **extra):
        item = {
            "question_id": "Q1",
            "observed_answer": "设一边为 x，另一边为 10−x；x(10−x)=24；解得 x=4 或 6，因此两边为 4 和 6。",
            "score_awarded": score,
            "deduction_reason": "",
        }
        if assessments is not None:
            item["step_assessments"] = assessments
        item.update(extra)
        return item

    def test_valid_step_assessments_are_normalized_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            grader = self._grader_with_three_step_question(Path(temp_dir))
            result = grader._validate_and_convert(
                {
                    "student_name": "余天策",
                    "total_score": 9,
                    "student_score": 6,
                    "needs_human_review": False,
                    "grading_details": [
                        self._process_detail(
                            6.0,
                            assessments=[
                                {
                                    "step_id": "S1",
                                    "achievement": "full",
                                    "score_awarded": 3,
                                    "student_evidence": "设一边为 x，另一边为 10−x",
                                    "missing_or_error": "",
                                    "reason": "边长和关系已建立。",
                                },
                                {
                                    "step_id": "S2",
                                    "achievement": "full",
                                    "score_awarded": 3,
                                    "student_evidence": "x(10−x)=24",
                                    "missing_or_error": "",
                                    "reason": "面积关系已联立。",
                                },
                                {
                                    "step_id": "S3",
                                    "achievement": "none",
                                    "score_awarded": 0,
                                    "student_evidence": "",
                                    "missing_or_error": "未写出求解过程与边长结果",
                                    "reason": "求解目标未达成。",
                                },
                            ],
                        )
                    ],
                },
                expected_student_name="余天策",
            )

            self.assertEqual(result.student_score, 6.0)
            metadata = result.raw_json["detail_metadata"]["Q1"]
            self.assertEqual(len(metadata["step_assessments"]), 3)
            self.assertEqual(metadata["step_assessments"][2]["score_awarded"], 0)
            self.assertEqual(metadata["step_assessments"][2]["achievement"], "none")
            self.assertIsNone(metadata["step_assessments_error"])

    def test_invalid_step_assessments_route_to_review_without_saving(self) -> None:
        bad_cases = [
            # 非整数得分
            [
                {
                    "step_id": "S1",
                    "achievement": "full",
                    "score_awarded": 2.5,
                    "student_evidence": "",
                    "missing_or_error": "",
                    "reason": "x",
                },
                {
                    "step_id": "S2",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "",
                    "missing_or_error": "",
                    "reason": "x",
                },
                {
                    "step_id": "S3",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "",
                    "missing_or_error": "",
                    "reason": "x",
                },
            ],
            # 未知步骤
            [
                {
                    "step_id": "S9",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "",
                    "missing_or_error": "",
                    "reason": "x",
                }
            ],
            # 重复步骤
            [
                {
                    "step_id": "S1",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "",
                    "missing_or_error": "",
                    "reason": "x",
                },
                {
                    "step_id": "S1",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "",
                    "missing_or_error": "",
                    "reason": "x",
                },
            ],
            # 漏块
            [
                {
                    "step_id": "S1",
                    "achievement": "full",
                    "score_awarded": 9,
                    "student_evidence": "",
                    "missing_or_error": "",
                    "reason": "x",
                }
            ],
            # 总和不守恒
            [
                {
                    "step_id": "S1",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "ev",
                    "missing_or_error": "",
                    "reason": "x",
                },
                {
                    "step_id": "S2",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "ev",
                    "missing_or_error": "",
                    "reason": "x",
                },
                {
                    "step_id": "S3",
                    "achievement": "full",
                    "score_awarded": 3,
                    "student_evidence": "ev",
                    "missing_or_error": "",
                    "reason": "x",
                },
            ],
        ]
        for index, assessments in enumerate(bad_cases):
            with tempfile.TemporaryDirectory() as temp_dir:
                grader = self._grader_with_three_step_question(Path(temp_dir))
                result = grader._validate_and_convert(
                    {
                        "student_name": "余天策",
                        "total_score": 9,
                        "student_score": 9,
                        "needs_human_review": False,
                        "grading_details": [
                            self._process_detail(
                                9.0 if index != 4 else 8.0,
                                assessments=assessments,
                            )
                        ],
                    },
                    expected_student_name="余天策",
                )
                self.assertTrue(result.needs_human_review, index)
                detail = result.grading_details[0]
                self.assertTrue(detail.error_category, index)
                metadata = result.raw_json["detail_metadata"]["Q1"]
                self.assertEqual(metadata["step_assessments"], [], index)
                self.assertTrue(metadata["step_assessments_error"], index)


_THREE_STEP_RUBRIC = {
    "questions": [
        {
            "question_id": "Q1",
            "question_type": "calculation",
            "max_score": 9,
            "answer_only_max_score": 1,
            "parts": [
                {
                    "part_id": "Q1",
                    "part_score": 9,
                    "response_mode": "process_required",
                    "steps": [
                        {"step_id": "S1", "step_score": 3, "core_goal": "建立和关系"},
                        {"step_id": "S2", "step_score": 3, "core_goal": "建立积关系"},
                        {"step_id": "S3", "step_score": 3, "core_goal": "求解"},
                    ],
                }
            ],
        }
    ],
}


def _step(
    step_id: str,
    achievement: str,
    score: int,
    evidence: str = "",
    missing: str = "",
    reason: str = "r",
) -> dict:
    return {
        "step_id": step_id,
        "achievement": achievement,
        "score_awarded": score,
        "student_evidence": evidence,
        "missing_or_error": missing,
        "reason": reason,
    }


class StepAssessmentContractTests(unittest.TestCase):


    def test_equivalent_requires_student_evidence(self) -> None:
        normalized, error = validate_step_assessments(
            [
                _step("S1", "full", 3, evidence="a+b=10"),
                _step("S2", "equivalent", 3, evidence="10a−a²=24 的等价式"),
                _step("S3", "full", 3, evidence="a=4,b=6"),
            ],
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=9,
        )
        self.assertIsNone(error)
        normalized, error = validate_step_assessments(
            [
                _step("S1", "full", 3, evidence="a+b=10"),
                _step("S2", "equivalent", 3),
                _step("S3", "full", 3, evidence="a=4,b=6"),
            ],
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=9,
        )
        self.assertIsNone(normalized)
        self.assertTrue(error)

    def test_carried_error_from_kept_for_earlier_failed_step(self) -> None:
        for reference_achievement in ("none", "uncertain"):
            normalized, error = validate_step_assessments(
                [
                    _step("S1", reference_achievement, 0, missing="计算错误"),
                    {
                        **_step("S2", "none", 0, missing="沿用 S1 错误结果"),
                        "carried_error_from": "S1",
                    },
                    _step("S3", "full", 3, evidence="a=4,b=6"),
                ],
                rubric=_THREE_STEP_RUBRIC,
                question_id="Q1",
                score_awarded=3,
            )
            self.assertIsNone(error, reference_achievement)
            self.assertEqual(
                normalized[1]["carried_error_from"], "S1", reference_achievement
            )
            self.assertNotIn("carried_error_from", normalized[0])
            self.assertNotIn("carried_error_from", normalized[2])


if __name__ == "__main__":
    unittest.main()
