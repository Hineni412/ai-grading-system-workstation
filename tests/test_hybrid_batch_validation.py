from __future__ import annotations

"""Hybrid batch validation tolerance and retry regressions."""

from hybrid_batch_grading_service import (
    MajorQuestionSpec,
    grade_major_question_batch,
    validate_hybrid_major_response,
)


def test_subjective_metadata_keeps_observed_answer_separate_from_evidence() -> None:
    from hybrid_batch_grading_service import _subjective_detail_metadata
    detail = {"observed_answer": "AB=AC", "evidence_steps": ["AB=AC", "∠B=∠C"]}
    metadata = _subjective_detail_metadata(detail, "Q12(P1)")
    assert metadata["observed_answer"] == "AB=AC"
    assert metadata["evidence_steps"] == ["AB=AC", "∠B=∠C"]


def test_explicit_subjective_review_is_preserved_with_high_confidence_and_other_category() -> None:
    from hybrid_batch_grading_service import _detail_from_ai_item, _build_result
    detail, error, metadata = _detail_from_ai_item({
        "question_id": "Q1", "score_awarded": 2, "confidence_score": 95,
        "needs_human_review": True, "error_category": "其他", "deduction_reason": "评分存在两种解释",
    }, ["Q1"], 80)
    assert not error
    assert metadata["needs_human_review"] is True
    result = _build_result("Synthetic", {"questions": [{"question_id": "Q1", "max_score": 6}]},
        6, [detail], [metadata], "synthetic")
    assert result.needs_human_review


def test_symbolic_and_expanded_proofs_preserve_the_model_score_without_literal_answer_matching() -> None:
    spec = MajorQuestionSpec(
        question_id="Q1",
        detail_question_ids=["Q1(P1)"],
        rubric={
            "question_id": "Q1", "question_type": "proof", "max_score": 6,
            "parts": [{
                "part_id": "Q1(P1)", "part_score": 6,
                "response_mode": "process_required", "allow_alternative_methods": False,
                "answer_only_max_score": 0,
                "steps": [
                    {"step_id": "S1", "step_score": 3, "core_goal": "核验三边平方关系"},
                    {"step_id": "S2", "step_score": 3, "core_goal": "据逆定理得到直角结论"},
                ],
            }],
        },
        answer_key={"canonical_answer": "5²+12²=13²，三角形为直角三角形"},
        max_score=6,
    )
    manifest = {"items": [{
        "paper_key": "synthetic_proof", "student_id": 1,
        "target_detail_question_ids": ["Q1(P1)"],
    }]}
    # The model owns mathematical evaluation; this validates score/evidence
    # transport, including a rejected answer, without asserting LLM accuracy.
    for observed, score in (
        ("AB=5、AC=12、BC=13；5²+12²=13²，故△ABC是直角三角形。", 6),
        ("AB=5、AC=12、BC=13；AB²+AC²=BC²，故△ABC是直角三角形。", 6),
        ("AB=5、AC=12、BC=13；BC²=AC²+AB²，故∠A=90°。", 6),
        ("AB=5、AC=12、BC=14；AB²+AC²=BC²，故△ABC是直角三角形。", 0),
    ):
        detail = {
            "question_id": "Q1(P1)", "score_awarded": score,
            "observed_answer": observed, "evidence_steps": [observed],
            "confidence_score": 95, "needs_human_review": False,
            "answer_is_blank_or_no_valid_work": False,
            "step_assessments": [{
                "step_id": step_id,
                "achievement": "full" if score else "none",
                "score_awarded": 3 if score else 0,
                "student_evidence": observed,
                "missing_or_error": "" if score else "给定三边不满足平方关系",
                "reason": "平方关系与结论成立" if score else "关系不成立，不能支持结论",
            } for step_id in ("S1", "S2")],
        }
        response = {"question_id": "Q1", "items": [{
            "paper_key": "synthetic_proof", "student_id": 1,
            "grading_details": [detail],
        }]}
        accepted, failed = validate_hybrid_major_response(response, manifest, spec)
        assert failed == []
        assert accepted[0]["details"][0].score_awarded == score
        assert accepted[0]["metadata"][0]["observed_answer"] == observed


_PROCESS_SPEC = MajorQuestionSpec(
    question_id="Q1",
    detail_question_ids=["Q1(P1)"],
    rubric={
        "question_id": "Q1", "question_type": "proof", "max_score": 6,
        "parts": [{
            "part_id": "Q1(P1)", "part_score": 6,
            "response_mode": "process_required",
            "steps": [
                {"step_id": "S1", "step_score": 3, "core_goal": "核验条件"},
                {"step_id": "S2", "step_score": 3, "core_goal": "得到结论"},
            ],
        }],
    },
    answer_key={"canonical_answer": "结论成立"},
    max_score=6,
)


def test_uncertain_step_point_forces_teacher_review() -> None:
    from hybrid_batch_grading_service import _detail_from_ai_item
    detail, error, metadata = _detail_from_ai_item(
        {
            "question_id": "Q1(P1)", "score_awarded": 6, "confidence_score": 95,
            "observed_answer": "∠A=90°，故三角形为直角三角形",
            "step_assessments": [
                {"step_id": "S1", "achievement": "full", "score_awarded": 3,
                 "student_evidence": "∠A=90°", "missing_or_error": "", "reason": "条件已给出"},
                {"step_id": "S2", "achievement": "uncertain", "score_awarded": 3,
                 "student_evidence": "字迹模糊的结论", "missing_or_error": "",
                 "reason": "看不清是否表达了等价结论"},
            ],
        },
        {"Q1(P1)"}, 80, spec=_PROCESS_SPEC,
    )
    assert not error
    assert detail is not None and detail.score_awarded == 6
    assert metadata["needs_human_review"] is True
    assert detail.error_category == "需复核"
    assert detail.error_summary == "uncertain_step_points"
    assert "S2" in (detail.deduction_reason or "")


def test_alternative_solution_forces_teacher_review() -> None:
    from hybrid_batch_grading_service import _detail_from_ai_item
    detail, error, metadata = _detail_from_ai_item(
        {
            "question_id": "Q1(P1)", "score_awarded": 6, "confidence_score": 95,
            "observed_answer": "以坐标法完成证明",
            "alternative_solution_detected": True,
            "alternative_solution_summary": "坐标法",
            "step_assessments": [
                {"step_id": "S1", "achievement": "full", "score_awarded": 3,
                 "student_evidence": "建立坐标系并核验条件", "missing_or_error": "", "reason": "条件达成"},
                {"step_id": "S2", "achievement": "full", "score_awarded": 3,
                 "student_evidence": "由坐标关系得到结论", "missing_or_error": "", "reason": "结论达成"},
            ],
        },
        {"Q1(P1)"}, 80, spec=_PROCESS_SPEC,
    )
    assert not error
    assert detail is not None
    assert metadata["needs_human_review"] is True
    assert detail.error_summary == "alternative_method_review"
    assert "参考答案之外的方法" in (detail.deduction_reason or "")


SPEC = MajorQuestionSpec(
    question_id="Q12",
    detail_question_ids=["Q12(P1)", "Q12(P2)", "Q12(P3)"],
    rubric={"question_id": "Q12", "max_score": 18},
    answer_key={},
    max_score=18,
)

MANIFEST = {
    "items": [
        {
            "paper_key": "paper_a",
            "student_id": 1,
            "target_detail_question_ids": ["Q12(P1)", "Q12(P2)", "Q12(P3)"],
        },
        {
            "paper_key": "paper_b",
            "student_id": 2,
            "target_detail_question_ids": ["Q12(P1)", "Q12(P2)", "Q12(P3)"],
        },
    ]
}


def _detail(qid: str, score: float, **extra) -> dict:
    detail = {
        "question_id": qid,
        "score_awarded": score,
        "confidence_score": 95,
        "answer_discarded_by_smudge": False,
        "answer_is_blank_or_no_valid_work": False,
        "needs_human_review": False,
        "deduction_reason": "",
    }
    detail.update(extra)
    return detail


def _item(paper_key: str, student_id: int, details: list[dict]) -> dict:
    return {
        "paper_key": paper_key,
        "student_id": student_id,
        "grading_details": details,
    }


def test_missing_top_level_question_id_is_salvaged_when_items_match() -> None:
    response = {
        "items": [
            _item("paper_a", 1, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 7), _detail("Q12(P3)", 6)]),
            _item("paper_b", 2, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 0), _detail("Q12(P3)", 0)]),
        ]
    }

    accepted, failed = validate_hybrid_major_response(response, MANIFEST, SPEC)

    assert [item["paper_key"] for item in accepted] == ["paper_a", "paper_b"]
    assert failed == []


def test_unusable_top_level_question_id_still_rejects_whole_batch() -> None:
    response = {
        "question_id": "Q11",
        "items": [
            _item("paper_a", 1, [_detail("Q11(P1)", 3)]),
        ],
    }

    accepted, failed = validate_hybrid_major_response(response, MANIFEST, SPEC)

    assert accepted == []
    assert len(failed) == 2
    assert {item["reason"] for item in failed} == {"question_id_mismatch"}


def test_duplicate_detail_keeps_first_result() -> None:
    response = {
        "question_id": "Q12",
        "items": [
            _item(
                "paper_a",
                1,
                [
                    _detail("Q12(P1)", 5),
                    _detail("Q12(P2)", 0),
                    _detail("Q12(P3)", 0),
                    _detail("Q12(P1)", 1),
                ],
            ),
        ],
    }

    accepted, failed = validate_hybrid_major_response(response, MANIFEST, SPEC)

    assert len(accepted) == 1
    scores = {d.question_id: d.score_awarded for d in accepted[0]["details"]}
    assert scores == {"Q12(P1)": 5.0, "Q12(P2)": 0.0, "Q12(P3)": 0.0}
    assert [item["paper_key"] for item in failed] == ["paper_b"]
    assert failed[0]["reason"] == "missing_paper_result"


def test_smudge_conflict_is_clamped_to_zero_and_flagged_for_review() -> None:
    response = {
        "question_id": "Q12",
        "items": [
            _item(
                "paper_a",
                1,
                [
                    _detail("Q12(P1)", 5),
                    _detail("Q12(P2)", 7),
                    _detail("Q12(P3)", 6, answer_discarded_by_smudge=True),
                ],
            ),
        ],
    }

    accepted, failed = validate_hybrid_major_response(response, MANIFEST, SPEC)

    assert len(accepted) == 1
    details = {d.question_id: d for d in accepted[0]["details"]}
    assert details["Q12(P3)"].score_awarded == 0.0
    assert details["Q12(P3)"].error_summary == "discarded_answer_scored"
    assert details["Q12(P1)"].score_awarded == 5.0
    assert details["Q12(P2)"].score_awarded == 7.0
    assert all(item["reason"] != "discarded_answer_scored" for item in failed)


def test_single_invalid_detail_no_longer_voids_siblings() -> None:
    response = {
        "question_id": "Q12",
        "items": [
            _item(
                "paper_a",
                1,
                [
                    _detail("Q12(P1)", 5),
                    {"question_id": "Q12(P2)", "score_awarded": None},
                    _detail("Q12(P3)", 6),
                ],
            ),
        ],
    }

    accepted, failed = validate_hybrid_major_response(response, MANIFEST, SPEC)

    assert len(accepted) == 1
    assert {d.question_id for d in accepted[0]["details"]} == {"Q12(P1)", "Q12(P3)"}
    partial = [item for item in failed if item["paper_key"] == "paper_a"]
    assert len(partial) == 1
    assert partial[0]["target_detail_question_ids"] == ["Q12(P2)"]


def test_item_with_no_valid_details_fails_as_a_whole() -> None:
    response = {
        "question_id": "Q12",
        "items": [
            _item(
                "paper_a",
                1,
                [
                    {"question_id": "Q12(P1)", "score_awarded": None},
                    {"question_id": "Q12(P2)", "score_awarded": None},
                    {"question_id": "Q12(P3)", "score_awarded": None},
                ],
            ),
        ],
    }

    accepted, failed = validate_hybrid_major_response(response, MANIFEST, SPEC)

    assert accepted == []
    whole = [item for item in failed if item["paper_key"] == "paper_a"]
    assert len(whole) == 1
    assert whole[0]["target_detail_question_ids"] == ["Q12(P1)", "Q12(P2)", "Q12(P3)"]


class _FakeBuilder:
    def __init__(self, atlas_path, manifest):
        self._atlas_path = atlas_path
        self._manifest = manifest

    def build(self, **_kwargs):
        return {"atlas_path": str(self._atlas_path), "manifest": self._manifest}


class _QueueClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def json_from_images_once(self, _static, _images, **kwargs):
        self.prompts.append(str(kwargs.get("dynamic_prompt") or ""))
        callback = kwargs.get("usage_callback")
        if callback:
            callback(None, {"model": "fake"})
        return self._responses.pop(0)


def _run_batch(tmp_path, responses):
    atlas = tmp_path / "atlas.png"
    atlas.write_bytes(b"\x89PNG\r\n\x1a\n")
    client = _QueueClient(responses)
    result = grade_major_question_batch(
        session_id="s1",
        spec=SPEC,
        paper_entries=[],
        answer_regions=[],
        llm_client=client,
        grading_model="fake",
        output_root=tmp_path,
        batch_index=0,
        builder=_FakeBuilder(atlas, MANIFEST),
    )
    return result, client


def test_validation_failure_retries_once_with_hint(tmp_path) -> None:
    bad = {"items": []}  # missing question_id and unusable payload
    good = {
        "question_id": "Q12",
        "items": [
            _item("paper_a", 1, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 7), _detail("Q12(P3)", 6)]),
            _item("paper_b", 2, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 0), _detail("Q12(P3)", 0)]),
        ],
    }

    result, client = _run_batch(tmp_path, [bad, good])

    assert len(client.prompts) == 2
    assert "上次响应未通过校验" in client.prompts[1]
    assert len(result["accepted"]) == 2
    assert result["failed"] == []
    assert result["usage"]["validation_attempts"] == 2


def test_clean_response_does_not_retry(tmp_path) -> None:
    good = {
        "question_id": "Q12",
        "items": [
            _item("paper_a", 1, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 7), _detail("Q12(P3)", 6)]),
            _item("paper_b", 2, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 0), _detail("Q12(P3)", 0)]),
        ],
    }

    result, client = _run_batch(tmp_path, [good])

    assert len(client.prompts) == 1
    assert result["failed"] == []
    assert result["usage"]["validation_attempts"] == 1


def test_retry_merge_keeps_papers_missing_from_second_attempt(tmp_path) -> None:
    first = {
        "question_id": "Q12",
        "items": [
            _item("paper_a", 1, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 7), _detail("Q12(P3)", 6)]),
        ],
    }
    second = {
        "question_id": "Q12",
        "items": [
            _item("paper_b", 2, [_detail("Q12(P1)", 5), _detail("Q12(P2)", 0), _detail("Q12(P3)", 0)]),
        ],
    }

    result, client = _run_batch(tmp_path, [first, second])

    assert len(client.prompts) == 2
    by_paper = {item["paper_key"]: item for item in result["accepted"]}
    assert set(by_paper) == {"paper_a", "paper_b"}
    scores_a = {d.question_id: d.score_awarded for d in by_paper["paper_a"]["details"]}
    assert scores_a["Q12(P2)"] == 7.0
    assert result["failed"] == []
