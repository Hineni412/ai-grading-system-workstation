import json
import tempfile
import unittest
from pathlib import Path

from ai_grader import AIGrader
from solution_answer_guard import (
    apply_solution_substance_rules,
    classify_non_substantive_solution_answer,
    has_solution_process_evidence,
)


class _FakeLLMClient:
    pass


class SolutionAnswerGuardTests(unittest.TestCase):
    def test_stem_echo_with_checkmark_is_non_substantive(self) -> None:
        for observed in ("(3)是不是定值", "(3)是不是定值√", "⑶是不是定值勾"):
            self.assertEqual(
                classify_non_substantive_solution_answer(observed),
                "仅复述题干，无有效作答",
                observed,
            )
            self.assertFalse(has_solution_process_evidence(observed), observed)

    def test_proof_with_real_work_keeps_evidence(self) -> None:
        observed = "∵OM是∠AOB的平分线，∴∠AOM=∠BOM，故△EPO≌△DPO"
        self.assertIsNone(classify_non_substantive_solution_answer(observed))
        self.assertTrue(has_solution_process_evidence(observed))

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
                                "parts": [{"part_id": "Q12(3)", "part_score": 4}],
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
                            "question_id": "Q12(3)",
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

    def test_conclusion_only_caps_to_answer_only_max(self) -> None:
        adjusted, category, summary, _reason = apply_solution_substance_rules(
            observed_answer="是定值",
            question_type="proof",
            full_score=4.0,
            answer_only_max_score=1,
            current_score=4.0,
        )
        self.assertEqual(adjusted, 1.0)
        self.assertEqual(category, "逻辑断裂")
        self.assertEqual(summary, "缺少有效过程")


if __name__ == "__main__":
    unittest.main()
