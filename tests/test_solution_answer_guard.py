import base64
import json
import tempfile
import unittest
from pathlib import Path

from ai_grader import AIGrader
from backend.config_generation.prompts import build_batch_generation_prompt
from solution_answer_guard import (
    answer_only_correct_flag,
    apply_solution_substance_rules,
    classify_non_substantive_solution_answer,
    has_solution_process_evidence,
    uncertain_step_ids,
    validate_step_assessments,
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
            rubric_path.write_text(
                json.dumps(
                    {
                        "questions": [
                            {
                                "question_id": "Q10",
                                "question_type": "visual_construction",
                                "max_score": 6,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
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

    def test_correct_answer_without_process_caps_to_one(self) -> None:
        adjusted, category, summary, _reason = apply_solution_substance_rules(
            observed_answer="两边为 4 和 6",
            question_type="calculation",
            full_score=9.0,
            answer_only_max_score=1,
            current_score=9.0,
            answer_only_correct=True,
        )
        self.assertEqual(adjusted, 1.0)
        self.assertEqual(category, "逻辑断裂")
        self.assertEqual(summary, "缺少有效过程")

    def test_wrong_answer_without_process_gets_zero(self) -> None:
        for current in (9.0, 3.0):
            adjusted, category, summary, reason = apply_solution_substance_rules(
                observed_answer="两边为 3 和 7",
                question_type="calculation",
                full_score=9.0,
                answer_only_max_score=1,
                current_score=current,
                answer_only_correct=False,
            )
            self.assertEqual(adjusted, 0.0, current)
            self.assertEqual(category, "逻辑断裂", current)
            self.assertEqual(summary, "答案错误且无有效过程", current)
            self.assertIn("0 分", reason or "", current)

    def test_undeclared_answer_correctness_keeps_one_point_cap(self) -> None:
        adjusted, _category, _summary, _reason = apply_solution_substance_rules(
            observed_answer="两边为 3 和 7",
            question_type="calculation",
            full_score=9.0,
            answer_only_max_score=1,
            current_score=5.0,
        )
        self.assertEqual(adjusted, 1.0)

    def test_answer_only_correct_flag_parsing(self) -> None:
        self.assertIs(answer_only_correct_flag(True), True)
        self.assertIs(answer_only_correct_flag(False), False)
        self.assertIs(answer_only_correct_flag("true"), True)
        self.assertIs(answer_only_correct_flag("False"), False)
        self.assertIsNone(answer_only_correct_flag(None))
        self.assertIsNone(answer_only_correct_flag("x=4"))
        self.assertIsNone(answer_only_correct_flag(1))

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
            self.assertEqual(
                metadata["step_assessments"][2]["achievement"], "none"
            )
            self.assertIsNone(metadata["step_assessments_error"])

    def test_invalid_step_assessments_route_to_review_without_saving(self) -> None:
        bad_cases = [
            # 非整数得分
            [{"step_id": "S1", "achievement": "full", "score_awarded": 2.5,
              "student_evidence": "", "missing_or_error": "", "reason": "x"},
             {"step_id": "S2", "achievement": "full", "score_awarded": 3,
              "student_evidence": "", "missing_or_error": "", "reason": "x"},
             {"step_id": "S3", "achievement": "full", "score_awarded": 3,
              "student_evidence": "", "missing_or_error": "", "reason": "x"}],
            # 未知步骤
            [{"step_id": "S9", "achievement": "full", "score_awarded": 3,
              "student_evidence": "", "missing_or_error": "", "reason": "x"}],
            # 重复步骤
            [{"step_id": "S1", "achievement": "full", "score_awarded": 3,
              "student_evidence": "", "missing_or_error": "", "reason": "x"},
             {"step_id": "S1", "achievement": "full", "score_awarded": 3,
              "student_evidence": "", "missing_or_error": "", "reason": "x"}],
            # 漏块
            [{"step_id": "S1", "achievement": "full", "score_awarded": 9,
              "student_evidence": "", "missing_or_error": "", "reason": "x"}],
            # 总和不守恒
            [{"step_id": "S1", "achievement": "full", "score_awarded": 3,
              "student_evidence": "ev", "missing_or_error": "", "reason": "x"},
             {"step_id": "S2", "achievement": "full", "score_awarded": 3,
              "student_evidence": "ev", "missing_or_error": "", "reason": "x"},
             {"step_id": "S3", "achievement": "full", "score_awarded": 3,
              "student_evidence": "ev", "missing_or_error": "", "reason": "x"}],
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
    def test_one_written_line_can_complete_two_distinct_steps(self) -> None:
        line = "a+b=10，ab=24"
        normalized, error = validate_step_assessments(
            [_step("S1", "equivalent", 3, evidence=line),
             _step("S2", "equivalent", 3, evidence=line),
             _step("S3", "none", 0, missing="未求解")],
            rubric=_THREE_STEP_RUBRIC, question_id="Q1", score_awarded=6,
        )
        self.assertIsNone(error)
        self.assertEqual(sum(step["score_awarded"] for step in normalized), 6)
        self.assertEqual(normalized[0]["student_evidence"], normalized[1]["student_evidence"])

    def test_all_steps_full_passes_when_sum_matches(self) -> None:
        normalized, error = validate_step_assessments(
            [
                _step("S1", "full", 3, evidence="a+b=10"),
                _step("S2", "full", 3, evidence="ab=24"),
                _step("S3", "full", 3, evidence="a=4,b=6"),
            ],
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=9,
        )
        self.assertIsNone(error)
        self.assertEqual(len(normalized or []), 3)

    def test_partial_achievement_is_rejected_as_retired(self) -> None:
        normalized, error = validate_step_assessments(
            [
                _step("S1", "full", 3, evidence="a+b=10"),
                _step("S2", "full", 3, evidence="ab=24"),
                _step("S3", "partial", 2, evidence="a=4", missing="缺一步"),
            ],
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=8,
        )
        self.assertIsNone(normalized)
        self.assertIn("已停用的部分分", error or "")
        self.assertIn("S3", error or "")

    def test_uncertain_with_best_judgement_score_passes(self) -> None:
        normalized, error = validate_step_assessments(
            [
                _step("S1", "full", 3, evidence="a+b=10"),
                _step("S2", "full", 3, evidence="ab=24"),
                _step("S3", "uncertain", 3, evidence="字迹模糊的 x=4"),
            ],
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=9,
        )
        self.assertIsNone(error)
        self.assertEqual(uncertain_step_ids(normalized), ["S3"])

    def test_uncertain_needs_best_judgement_score_and_a_basis(self) -> None:
        for achievement_score, evidence, missing in (
            (2, "ev", ""),   # 非 0 非满分的中间分
            (0, "", ""),     # 没有依据也没有缺漏
        ):
            normalized, error = validate_step_assessments(
                [
                    _step("S1", "full", 3, evidence="a+b=10"),
                    _step("S2", "full", 3, evidence="ab=24"),
                    _step("S3", "uncertain", achievement_score, evidence=evidence, missing=missing),
                ],
                rubric=_THREE_STEP_RUBRIC,
                question_id="Q1",
                score_awarded=6 + achievement_score,
            )
            self.assertIsNone(normalized, (achievement_score, evidence, missing))
            self.assertTrue(error, (achievement_score, evidence, missing))

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

    def test_none_requires_zero_score_and_missing_or_error(self) -> None:
        normalized, error = validate_step_assessments(
            [
                _step("S1", "full", 3, evidence="a+b=10"),
                _step("S2", "full", 3, evidence="ab=24"),
                _step("S3", "none", 1, missing="未求解"),
            ],
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=7,
        )
        self.assertIsNone(normalized)
        self.assertTrue(error)
        normalized, error = validate_step_assessments(
            [
                _step("S1", "full", 3, evidence="a+b=10"),
                _step("S2", "full", 3, evidence="ab=24"),
                _step("S3", "none", 0),
            ],
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=6,
        )
        self.assertIsNone(normalized)
        self.assertTrue(error)

    def test_answer_only_correct_requires_all_steps_none(self) -> None:
        all_none = [
            _step("S1", "none", 0, missing="未写过程"),
            _step("S2", "none", 0, missing="未写过程"),
            _step("S3", "none", 0, missing="只有答案"),
        ]
        normalized, error = validate_step_assessments(
            all_none,
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=1,
            answer_only_correct=True,
        )
        self.assertIsNone(error)
        self.assertEqual(len(normalized or []), 3)

        one_full = all_none[:2] + [
            _step("S3", "full", 3, evidence="a=4,b=6"),
        ]
        normalized, error = validate_step_assessments(
            one_full,
            rubric=_THREE_STEP_RUBRIC,
            question_id="Q1",
            score_awarded=1,
            answer_only_correct=True,
        )
        self.assertIsNone(normalized)
        self.assertIn("矛盾", error or "")
        self.assertIn("S3", error or "")

    def test_prompts_use_checkpoint_wording(self) -> None:
        self.assertIn("uncertain", SHARED_GRADING_RULES)
        self.assertNotIn("给整数部分分", SHARED_GRADING_RULES)

        batch_prompt = build_batch_generation_prompt(
            ["Q12"],
            [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "question_text": "证明两条线段相等。",
                    "answer_text": "略",
                }
            ],
            [],
            "",
        )
        self.assertIn("适用前提", batch_prompt)
        self.assertIn("判定点", batch_prompt)

        spec = MajorQuestionSpec(
            question_id="Q1",
            detail_question_ids=["Q1"],
            rubric={"question_id": "Q1", "parts": [{"part_id": "Q1"}]},
            answer_key={},
            max_score=6,
        )
        system_prompt, _static, _dynamic = build_hybrid_major_prompt(spec, {"items": []})
        self.assertIn("只判有/无", system_prompt)

    def test_training_criteria_prompt_lists_precondition_checkpoints(self) -> None:
        from question_bank.models.tag_schema import TaggingContext
        from question_bank.training_criteria.adapters import _combined_prompt
        from question_bank.training_criteria.analysis import (
            PlannedAnalysisBatch,
            QuestionAnalysisInput,
        )

        batch = PlannedAnalysisBatch(
            questions=(
                QuestionAnalysisInput(
                    question_id=1,
                    tagging_context=TaggingContext(question_text="证明两条线段相等。"),
                ),
            ),
            estimated_input_tokens=0,
            estimated_output_tokens=0,
        )
        text = _combined_prompt(batch, "both")[1]["content"][0]["text"]
        self.assertIn("适用前提", text)
        self.assertIn("observable_evidence", text)


if __name__ == "__main__":
    unittest.main()


