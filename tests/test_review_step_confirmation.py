from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from backend.review.service import (
    ReviewApplicationService,
    ReviewConfirmationInput,
    ReviewRevisionConflictError,
    ReviewValidationError,
)
from backend.review.manual_review_service import ManualReviewService
from tests.test_review_media_service import _seed_media


def seed_step_review(tmp_path):
    seed = _seed_media(tmp_path)
    rubric_path = seed.data_root / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "calculation",
                        "max_score": 7,
                        "parts": [
                            {
                                "part_id": "Q1",
                                "part_score": 7,
                                "response_mode": "process_required",
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "step_score": 4,
                                        "core_goal": "establish relation",
                                        "evidence_point_ids": ["p1"],
                                    },
                                    {
                                        "step_id": "S2",
                                        "step_score": 3,
                                        "core_goal": "calculate result",
                                        "evidence_point_ids": ["p2"],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET rubric_path=? WHERE id=?",
            (str(rubric_path), seed.session_id),
        )
        conn.execute(
            "UPDATE session_results SET total_score=7, student_score=7, raw_json=?",
            (
                json.dumps(
                    {
                        "detail_metadata": {
                            "Q1": {
                                "step_assessments": [
                                    {
                                        "step_id": "S1",
                                        "achievement": "full",
                                        "score_awarded": 4,
                                    }
                                ]
                            }
                        }
                    }
                ),
            ),
        )
        conn.execute("UPDATE session_details SET score_awarded=7, ai_score_awarded=7")
    session = seed.db.sessions.get_grading_session(seed.session_id)
    context = {
        "scan_batch_id": "test-step-batch",
        "papers": [{"student_id": 1, "target_type": "group", "target_id": "test"}],
    }
    service = ReviewApplicationService(
        seed.db, ManualReviewService(seed.db, seed.annotated_dir)
    )
    return seed, session, context, service


def confirmation(seed, **overrides):
    return ReviewConfirmationInput(
        result_id=seed.result_id,
        detail_id=seed.detail_id,
        score_awarded=6,
        step_scores=[
            {"part_id": "Q1", "step_id": "S1", "score_awarded": 4},
            {"part_id": "Q1", "step_id": "S2", "score_awarded": 2},
        ],
        **overrides,
    )


def test_original_cleanup_keeps_teacher_score_lock_and_allows_later_confirmation(tmp_path):
    from backend.files.session_originals import clear_session_originals
    seed, session, context, service = seed_step_review(tmp_path)
    service.confirm(seed.session_id, session, "Q1", [confirmation(seed)], manual_context=context, defer_annotations=True)
    (before,) = service.list_items(seed.session_id, session, manual_context=context)
    clear_session_originals(seed.db, seed.data_root, seed.session_id, clear_crop_cache=lambda: 0)
    (retained,) = service.list_items(seed.session_id, session, manual_context=context)
    assert retained.teacher_locked and retained.score_awarded == before.score_awarded
    assert retained.revision == before.revision and retained.metadata["teacher_review"] == before.metadata["teacher_review"]
    request = replace(confirmation(seed), expected_revision=retained.revision, score_awarded=5, step_scores=None)
    service.confirm(seed.session_id, session, "Q1", [request], manual_context=context, defer_annotations=True)
    (after,) = service.list_items(seed.session_id, session, manual_context=context)
    assert after.teacher_locked and after.score_awarded == 5 and after.revision == retained.revision + 1
    assert not seed.front_path.exists()


def test_step_confirmation_roundtrip_partial_means_not_achieved_and_total_only_clears_steps(
    tmp_path,
):
    seed, session, context, service = seed_step_review(tmp_path)
    outcome = service.confirm(
        seed.session_id,
        session,
        "Q1",
        [confirmation(seed)],
        manual_context=context,
        defer_annotations=True,
    )
    assert outcome.updated_details == 1
    assert outcome.annotation_outcomes[0].status == "on_demand"
    (item,) = service.list_items(seed.session_id, session, manual_context=context)
    assert item.score_awarded == 6 and item.revision == 1
    assert item.metadata["ai_score_awarded"] == 7
    # 无批语且仍有扣分：明细保留原理由，教师锁存确认占位。
    assert _detail_reason_and_lock(seed) == ((6.0, "需复核"), "人工复核已确认")
    assert item.needs_review is False
    review = item.metadata["teacher_review"]
    assert [(s["score_awarded"], s["achievement"]) for s in review["steps"]] == [
        (4, "full"),
        (2, "none"),
    ]
    assert review["steps"][1]["evidence_point_ids"] == ["p2"]
    rows = seed.db.results.get_active_assessment_evidence(
        student_ids=["1"], session_ids=[seed.session_id]
    )
    assert rows[0]["assessment_state"]["teacher_review"] == review
    with pytest.raises(ReviewRevisionConflictError):
        service.confirm(
            seed.session_id,
            session,
            "Q1",
            [confirmation(seed)],
            manual_context=context,
            defer_annotations=True,
        )
    (unchanged,) = service.list_items(seed.session_id, session, manual_context=context)
    assert unchanged.metadata["teacher_review"] == review
    total_only = replace(
        confirmation(seed), expected_revision=1, score_awarded=5, step_scores=None
    )
    service.confirm(
        seed.session_id,
        session,
        "Q1",
        [total_only],
        manual_context=context,
        defer_annotations=True,
    )
    (final,) = service.list_items(seed.session_id, session, manual_context=context)
    assert (
        final.score_awarded == 5
        and final.revision == 2
        and "teacher_review" not in final.metadata
    )


def test_teacher_steps_survive_full_ai_regrade_and_missing_ai_metadata(tmp_path):
    from backend.domain_models import GradingResult, QuestionGradingDetail

    seed, session, context, service = seed_step_review(tmp_path)
    service.confirm(
        seed.session_id,
        session,
        "Q1",
        [confirmation(seed)],
        manual_context=context,
        defer_annotations=True,
    )
    with sqlite3.connect(seed.db.db_path) as conn:
        paper_id = conn.execute(
            "SELECT paper_id FROM session_results WHERE id=?", (seed.result_id,)
        ).fetchone()[0]
    incoming = GradingResult(
        student_name="test",
        total_score=7,
        student_score=0,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id="Q1",
                score_awarded=0,
                deduction_reason="new AI",
                knowledge_ids=["K1"],
            )
        ],
        raw_json={"teacher_reviews": {"Q1": {"revision": 999, "steps": []}}},
    )
    seed.db.results.save_session_result(
        seed.session_id, 1, paper_id, incoming, scan_batch_id=context["scan_batch_id"]
    )
    (final,) = service.list_items(seed.session_id, session, manual_context=context)
    assert final.score_awarded == 6 and final.metadata["ai_score_awarded"] == 0
    assert final.metadata["teacher_review"]["revision"] == 1
    assert final.metadata["teacher_review"]["steps"][1]["score_awarded"] == 2


def _detail_reason_and_lock(seed):
    with sqlite3.connect(seed.db.db_path) as conn:
        detail = conn.execute(
            "SELECT score_awarded, deduction_reason FROM session_details WHERE id=?",
            (seed.detail_id,),
        ).fetchone()
        lock = conn.execute(
            "SELECT deduction_reason FROM teacher_score_locks WHERE session_id=?",
            (seed.session_id,),
        ).fetchone()
    return detail, lock[0]


def test_old_annotation_cleanup_failure_does_not_report_a_failed_save(
    tmp_path, monkeypatch
):
    seed, session, context, service = seed_step_review(tmp_path)

    def fail_cleanup(_paths):
        raise sqlite3.OperationalError("synthetic cleanup unavailable")

    monkeypatch.setattr(
        service.manual_review_service.review,
        "referenced_annotation_paths",
        fail_cleanup,
    )
    result = service.confirm(
        seed.session_id,
        session,
        "Q1",
        [confirmation(seed)],
        manual_context=context,
        defer_annotations=True,
    )
    assert result.updated_details == 1
    (item,) = service.list_items(seed.session_id, session, manual_context=context)
    assert item.score_awarded == 6 and item.revision == 1


@pytest.mark.parametrize("ai_score,teacher_score,note,source,expected_note", [
    (0, 4, "ignored", "none", None),
    (0, 2, "ignored", "ai", None),
    (4, 2, "  教师指出计算错误  ", "teacher", "教师指出计算错误"),
    (None, 2, "  ", "teacher", None),
    (4, 4, "ignored", "none", None),
])
def test_teacher_step_deduction_rules_roundtrip(tmp_path, ai_score, teacher_score, note, source, expected_note):
    seed, session, context, service = seed_step_review(tmp_path)
    with sqlite3.connect(seed.db.db_path) as conn:
        ai = {"part_id": "Q1", "step_id": "S1", "score_awarded": ai_score,
              "reason": "AI 理由", "missing_or_error": "缺失关系", "student_evidence": "原作答"}
        conn.execute("UPDATE session_results SET raw_json=? WHERE id=?",
                     (json.dumps({"detail_metadata": {"Q1": {"step_assessments": [ai]}}}), seed.result_id))
    request = replace(confirmation(seed), score_awarded=teacher_score + 3, step_scores=[
        {"part_id": "Q1", "step_id": "S1", "score_awarded": teacher_score, "teacher_note": note, "carried_error_from": None},
        {"part_id": "Q1", "step_id": "S2", "score_awarded": 3},
    ])
    service.confirm(seed.session_id, session, "Q1", [request], manual_context=context, defer_annotations=True)
    (item,) = service.list_items(seed.session_id, session, manual_context=context)
    step = item.metadata["teacher_review"]["steps"][0]
    assert step["ai_score_awarded"] == ai_score
    assert step["deduction_source"] == source
    assert step.get("teacher_note") == expected_note
    for field, value in (("reason", "AI 理由"), ("missing_or_error", "缺失关系"), ("student_evidence", "原作答")):
        assert step.get(field) == (value if source == "ai" else None)


@pytest.mark.parametrize("first,second,override,expected", [
    (0, 0, {}, "S1"), (4, 0, {}, None),
    (0, 0, {"carried_error_from": None}, None),
    (0, 0, {"carried_error_from": "OTHER"}, None),
    (0, 1, {"carried_error_from": "S1"}, None),
    (0, 0, {"carried_error_from": "S2"}, None),
    (0, 0, {"carried_error_from": "S1"}, "S1"),
])
def test_carried_error_validates_teacher_scores_and_explicit_null(tmp_path, first, second, override, expected):
    from backend.review.service import _normalize_teacher_steps
    seed, session, context, service = seed_step_review(tmp_path)
    (item,) = service.list_items(seed.session_id, session, manual_context=context)
    item = replace(item, metadata={"step_assessments": [
        {"part_id": "Q1", "step_id": "S2", "score_awarded": 0, "carried_error_from": "S1"}]})
    rubric = json.loads((seed.data_root / "rubric.json").read_text(encoding="utf-8"))
    normalized = _normalize_teacher_steps([
        {"part_id": "Q1", "step_id": "S1", "score_awarded": first},
        {"part_id": "Q1", "step_id": "S2", "score_awarded": second, **override},
    ], rubric, item, first + second)
    assert normalized[1].get("carried_error_from") == expected
    assert "carried_error_from" not in _normalize_teacher_steps([
        {"part_id": "Q1", "step_id": "S1", "score_awarded": 0, "carried_error_from": "S2"},
        {"part_id": "Q1", "step_id": "S2", "score_awarded": 0},
    ], rubric, item, 0)[0]
    rubric["questions"][0]["parts"] = [
        {"part_id": "P1", "part_score": 4, "steps": [{"step_id": "S1", "step_score": 4}]},
        {"part_id": "P2", "part_score": 3, "steps": [{"step_id": "S2", "step_score": 3}]},
    ]
    normalized = _normalize_teacher_steps([
        {"part_id": "P1", "step_id": "S1", "score_awarded": 0},
        {"part_id": "P2", "step_id": "S2", "score_awarded": 0, "carried_error_from": "S1"},
    ], rubric, item, 0)
    assert "carried_error_from" not in normalized[1]


@pytest.mark.parametrize("scores,total", [([4, 2], 7), ([4, 3.5], 7.5), ([5, 0], 5), ([-1, 3], 2)])
def test_step_validation_still_rejects_sum_fraction_and_range(tmp_path, scores, total):
    from backend.review.service import _normalize_teacher_steps
    seed, session, context, service = seed_step_review(tmp_path)
    (item,) = service.list_items(seed.session_id, session, manual_context=context)
    rubric = json.loads((seed.data_root / "rubric.json").read_text(encoding="utf-8"))
    with pytest.raises(ReviewValidationError):
        _normalize_teacher_steps([
            {"part_id": "Q1", "step_id": "S1", "score_awarded": scores[0]},
            {"part_id": "Q1", "step_id": "S2", "score_awarded": scores[1]},
        ], rubric, item, total)
