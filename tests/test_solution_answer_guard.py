import base64
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
from scoring_prompt_rules import SHARED_GRADING_RULES
from hybrid_batch_grading_service import MajorQuestionSpec, build_hybrid_major_prompt


class _FakeLLMClient:
    pass


class SolutionAnswerGuardTests(unittest.TestCase):
    def test_shared_prompt_rules_distinguish_response_modes(self) -> None:
        self.assertIn("short_answer_points", SHARED_GRADING_RULES)
        self.assertIn("visual_construction", SHARED_GRADING_RULES)
        self.assertIn("process_required", SHARED_GRADING_RULES)
        self.assertIn("不得读取或评分学生自己作废的内容", SHARED_GRADING_RULES)
        self.assertIn("红笔教师批注", SHARED_GRADING_RULES)
        self.assertIn("逐空、逐步独立评分", SHARED_GRADING_RULES)
        for mixed_fragment in (
            "Shared grading rules",
            "Objective items",
            "Student-discarded content",
            "Only process_required parts require",
        ):
            self.assertNotIn(mixed_fragment, SHARED_GRADING_RULES)

    def test_answer_images_are_sent_as_images_not_embedded_prompt_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rubric_path = root / "rubric.json"
            answer_path = root / "answer.json"
            rubric_path.write_text(json.dumps({"questions": []}), encoding="utf-8")
            encoded = base64.b64encode(b"perfect-answer-image").decode("ascii")
            answer_path.write_text(
                json.dumps({"questions": [{"question_id": "Q10", "answer_image_base64": encoded}]}),
                encoding="utf-8",
            )

            grader = AIGrader(rubric_path, _FakeLLMClient(), answer_key_path=answer_path)

            self.assertEqual(grader._reference_answer_images(), [("Q10", b"perfect-answer-image")])
            self.assertNotIn(encoded, grader._build_system_prompt())
            self.assertIn("Q10", grader._build_user_prompt("学生", reference_question_ids=["Q10"]))

    def test_hybrid_prompt_omits_embedded_image_base64(self) -> None:
        encoded = base64.b64encode(b"perfect-answer-image").decode("ascii")
        spec = MajorQuestionSpec(
            question_id="Q10",
            detail_question_ids=["Q10"],
            rubric={"question_id": "Q10", "parts": [{"part_id": "Q10", "response_mode": "visual_construction"}]},
            answer_key={"question_id": "Q10", "answer_image_base64": encoded},
            max_score=6,
        )

        system_prompt, static_prompt, dynamic_prompt = build_hybrid_major_prompt(spec, {"items": []}, has_rubric_image=True)
        prompt = f"{system_prompt}\n{static_prompt}\n{dynamic_prompt}"

        self.assertNotIn(encoded, prompt)
        self.assertIn("visual_construction", prompt)

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

    def test_direct_answer_part_inside_comprehensive_question_is_not_process_capped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rubric_path = root / "rubric.json"
            rubric_path.write_text(
                json.dumps(
                    {
                        "total_score": 4,
                        "questions": [
                            {
                                "question_id": "Q11",
                                "question_type": "comprehensive",
                                "max_score": 4,
                                "answer_only_max_score": 1,
                                "parts": [
                                    {
                                        "part_id": "Q11",
                                        "part_score": 4,
                                        "response_mode": "short_answer_points",
                                        "answer_only_max_score": 4,
                                    }
                                ],
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
                            "question_id": "Q11",
                            "observed_answer": "72°，54°",
                            "score_awarded": 4,
                            "deduction_reason": "",
                            "knowledge_id": "K1",
                            "knowledge_ids": ["K1"],
                        }
                    ],
                },
                expected_student_name="余天策",
            )

            self.assertEqual(result.student_score, 4)
            self.assertEqual(result.grading_details[0].score_awarded, 4)

    def test_full_paper_grading_extracts_aligned_metadata(self) -> None:
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
                                "max_score": 4,
                                "parts": [{"part_id": "Q12", "part_score": 4}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            grader = AIGrader(rubric_path, _FakeLLMClient())
            
            mock_response = {
                "student_name": "余天策",
                "total_score": 4,
                "student_score": 3,
                "needs_human_review": False,
                "grading_details": [
                    {
                        "question_id": "Q12",
                        "observed_answer": "∵证明过程",
                        "score_awarded": 3.0,
                        "deduction_reason": "缺少最后证明结论",
                        "confidence_score": 75.0,
                        "error_category": "逻辑断裂",
                        "error_summary": "没有得出最终结论",
                        "evidence_steps": ["step1", "step2"],
                        "missing_steps": ["step3"],
                        "candidate_scores": [
                            {"score": 3.0, "confidence": 0.75, "reason": "过程正确但缺结论"},
                            {"score": 2.0, "confidence": 0.25, "reason": "严格扣分"}
                        ],
                        "alternative_solution_detected": True,
                        "alternative_solution_summary": "用解析几何法",
                        "answer_discarded_by_smudge": False,
                        "answer_is_blank_or_no_valid_work": False,
                    }
                ],
            }
            
            result = grader._validate_and_convert(mock_response, expected_student_name="余天策")
            
            self.assertTrue(result.needs_human_review)
            self.assertIn("detail_metadata", result.raw_json)
            detail_metadata = result.raw_json["detail_metadata"]
            self.assertIn("Q12", detail_metadata)
            
            meta = detail_metadata["Q12"]
            self.assertEqual(meta["evidence_steps"], ["step1", "step2"])
            self.assertEqual(meta["missing_steps"], ["step3"])
            self.assertEqual(len(meta["candidate_scores"]), 2)
            self.assertEqual(meta["candidate_scores"][0]["score"], 3.0)
            self.assertTrue(meta["alternative_solution_detected"])
            self.assertEqual(meta["alternative_solution_summary"], "用解析几何法")
            self.assertFalse(meta["answer_discarded_by_smudge"])
            self.assertFalse(meta["answer_is_blank_or_no_valid_work"])


if __name__ == "__main__":
    unittest.main()


