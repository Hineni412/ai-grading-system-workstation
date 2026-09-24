from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from backend.review.service import ReviewApplicationService, ReviewConfirmationInput, ReviewRevisionConflictError, ReviewValidationError
from manual_review_service import ManualReviewService
from tests.test_review_media_service import _seed_media


def seed_step_review(tmp_path):
    seed = _seed_media(tmp_path)
    rubric_path = seed.data_root / "rubric.json"
    rubric_path.write_text(json.dumps({"questions": [{
        "question_id": "Q1", "question_type": "calculation", "max_score": 7,
        "parts": [{"part_id": "Q1", "part_score": 7, "response_mode": "process_required",
                   "steps": [{"step_id": "S1", "step_score": 4, "core_goal": "establish relation", "evidence_point_ids": ["p1"]},
                             {"step_id": "S2", "step_score": 3, "core_goal": "calculate result", "evidence_point_ids": ["p2"]}]}],
    }]}), encoding="utf-8")
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute("UPDATE grading_sessions SET rubric_path=? WHERE id=?", (str(rubric_path), seed.session_id))
        conn.execute("UPDATE session_results SET total_score=7, student_score=7, raw_json=?", (json.dumps({"detail_metadata": {"Q1": {"step_assessments": [{"step_id": "S1", "achievement": "full", "score_awarded": 4}]}}}),))
        conn.execute("UPDATE session_details SET score_awarded=7, ai_score_awarded=7")
    session = seed.db.get_grading_session(seed.session_id)
    context = {"scan_batch_id": "test-step-batch", "papers": [{"student_id": 1, "target_type": "group", "target_id": "test"}]}
    service = ReviewApplicationService(seed.db, ManualReviewService(seed.db, seed.annotated_dir))
    return seed, session, context, service


def confirmation(seed, **overrides):
    return ReviewConfirmationInput(result_id=seed.result_id, detail_id=seed.detail_id, score_awarded=6,
        step_scores=[{"part_id": "Q1", "step_id": "S1", "score_awarded": 4},
                     {"part_id": "Q1", "step_id": "S2", "score_awarded": 2}], **overrides)


def test_step_confirmation_roundtrip_partial_means_not_achieved_and_total_only_clears_steps(tmp_path):
    seed, session, context, service = seed_step_review(tmp_path)
    outcome = service.confirm(seed.session_id, session, "Q1", [confirmation(seed)], manual_context=context, defer_annotations=True)
    assert outcome.updated_details == 1
    assert outcome.annotation_outcomes[0].status == "on_demand"
    item, = service.list_items(seed.session_id, session, manual_context=context)
    assert item.score_awarded == 6 and item.revision == 1
    assert item.metadata["ai_score_awarded"] == 7
    review = item.metadata["teacher_review"]
    assert [(s["score_awarded"], s["achievement"]) for s in review["steps"]] == [(4, "full"), (2, "none")]
    assert review["steps"][1]["evidence_point_ids"] == ["p2"]
    rows = seed.db.get_active_assessment_evidence(student_ids=["1"], session_ids=[seed.session_id])
    assert rows[0]["assessment_state"]["teacher_review"] == review
    with pytest.raises(ReviewRevisionConflictError):
        service.confirm(seed.session_id, session, "Q1", [confirmation(seed)], manual_context=context, defer_annotations=True)
    unchanged, = service.list_items(seed.session_id, session, manual_context=context)
    assert unchanged.metadata["teacher_review"] == review
    total_only = replace(confirmation(seed), expected_revision=1, score_awarded=5, step_scores=None)
    service.confirm(seed.session_id, session, "Q1", [total_only], manual_context=context, defer_annotations=True)
    final, = service.list_items(seed.session_id, session, manual_context=context)
    assert final.score_awarded == 5 and final.revision == 2 and "teacher_review" not in final.metadata


@pytest.mark.parametrize("change", ["missing", "duplicate", "fraction", "over", "sum"])
def test_invalid_steps_do_not_save_any_score(tmp_path, change):
    seed, session, context, service = seed_step_review(tmp_path)
    request = confirmation(seed)
    if change == "missing": request.step_scores.pop()
    elif change == "duplicate": request.step_scores[1] = request.step_scores[0]
    elif change == "fraction": request.step_scores[0]["score_awarded"] = 3.5
    elif change == "over": request.step_scores[0]["score_awarded"] = 5
    else: request = replace(request, score_awarded=5)
    with pytest.raises(ReviewValidationError):
        service.confirm(seed.session_id, session, "Q1", [request], manual_context=context, defer_annotations=True)
    assert seed.db.list_teacher_score_locks(seed.session_id) == []
    assert seed.db.get_result_details(seed.result_id)[0]["score_awarded"] == 7


def test_save_returns_before_render_and_viewing_annotation_renders_once(tmp_path, monkeypatch):
    import manual_review_service as module
    seed, session, context, service = seed_step_review(tmp_path)
    renders = []
    original = module.render_annotated_paper
    def counted(**kwargs):
        renders.append(kwargs["question_scores"]["Q1"]["score_awarded"])
        return original(**kwargs)
    monkeypatch.setattr(module, "render_annotated_paper", counted)
    service.confirm(seed.session_id, session, "Q1", [confirmation(seed)], manual_context=context, defer_annotations=True)
    assert renders == []
    assert seed.db.get_annotated_result(seed.result_id)["annotated_front_path"] is None
    for side in ("front", "back"):
        assert seed.service.resolve_result_page(seed.session_id, seed.result_id, side, "annotated").path.exists()
    assert renders == [6]


def test_preflight_crop_reuses_existing_cache(tmp_path, monkeypatch):
    import backend.media.service as module
    seed = _seed_media(tmp_path)
    first = seed.service.render_preflight_crop(seed.session_id, "Q1", front_source=seed.front_path, back_source=seed.back_path)
    monkeypatch.setattr(module, "_render_crop_jpeg", lambda *_: pytest.fail("cached crop must not decode full page again"))
    assert seed.service.render_preflight_crop(seed.session_id, "Q1", front_source=seed.front_path, back_source=seed.back_path) == first


def test_teacher_steps_survive_full_ai_regrade_and_missing_ai_metadata(tmp_path):
    from backend.domain_models import GradingResult, QuestionGradingDetail
    seed, session, context, service = seed_step_review(tmp_path)
    service.confirm(seed.session_id, session, "Q1", [confirmation(seed)], manual_context=context, defer_annotations=True)
    with sqlite3.connect(seed.db.db_path) as conn:
        paper_id = conn.execute("SELECT paper_id FROM session_results WHERE id=?", (seed.result_id,)).fetchone()[0]
    incoming = GradingResult(student_name="test", total_score=7, student_score=0, needs_human_review=False,
        grading_details=[QuestionGradingDetail(question_id="Q1", score_awarded=0, deduction_reason="new AI", knowledge_ids=["K1"])],
        raw_json={"teacher_reviews": {"Q1": {"revision": 999, "steps": []}}})
    seed.db.result_repository.save_session_result(seed.session_id, 1, paper_id, incoming, scan_batch_id=context["scan_batch_id"])
    final, = service.list_items(seed.session_id, session, manual_context=context)
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


def test_confirm_without_note_partial_score_keeps_ai_reason(tmp_path):
    """无批语且仍有扣分：明细保留 AI 原理由，锁表存占位。"""
    seed, session, context, service = seed_step_review(tmp_path)
    service.confirm(
        seed.session_id, session, "Q1",
        [ReviewConfirmationInput(result_id=seed.result_id, detail_id=seed.detail_id, score_awarded=6)],
        manual_context=context, defer_annotations=True,
    )
    detail, lock_reason = _detail_reason_and_lock(seed)
    assert detail == (6.0, "需复核")
    assert lock_reason == "人工复核已确认"


def test_confirm_without_note_full_score_writes_placeholder(tmp_path):
    seed, session, context, service = seed_step_review(tmp_path)
    service.confirm(
        seed.session_id, session, "Q1",
        [ReviewConfirmationInput(result_id=seed.result_id, detail_id=seed.detail_id, score_awarded=7)],
        manual_context=context, defer_annotations=True,
    )
    detail, lock_reason = _detail_reason_and_lock(seed)
    assert detail == (7.0, "人工复核已确认")
    assert lock_reason == "人工复核已确认"


def test_confirm_with_note_writes_note_to_detail_and_lock(tmp_path):
    seed, session, context, service = seed_step_review(tmp_path)
    service.confirm(
        seed.session_id, session, "Q1",
        [ReviewConfirmationInput(result_id=seed.result_id, detail_id=seed.detail_id,
                                 score_awarded=6, deduction_reason="漏写单位")],
        manual_context=context, defer_annotations=True,
    )
    detail, lock_reason = _detail_reason_and_lock(seed)
    assert detail == (6.0, "漏写单位")
    assert lock_reason == "漏写单位"


def test_confirm_without_note_blank_prior_reason_writes_placeholder(tmp_path):
    seed, session, context, service = seed_step_review(tmp_path)
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute("UPDATE session_details SET deduction_reason=NULL WHERE id=?", (seed.detail_id,))
    service.confirm(
        seed.session_id, session, "Q1",
        [ReviewConfirmationInput(result_id=seed.result_id, detail_id=seed.detail_id, score_awarded=6)],
        manual_context=context, defer_annotations=True,
    )
    detail, lock_reason = _detail_reason_and_lock(seed)
    assert detail == (6.0, "人工复核已确认")
    assert lock_reason == "人工复核已确认"


def test_old_annotation_cleanup_failure_does_not_report_a_failed_save(tmp_path, monkeypatch):
    seed, session, context, service = seed_step_review(tmp_path)
    def fail_cleanup(_paths):
        raise sqlite3.OperationalError("synthetic cleanup unavailable")
    monkeypatch.setattr(service.manual_review_service.review, "referenced_annotation_paths", fail_cleanup)
    result = service.confirm(seed.session_id, session, "Q1", [confirmation(seed)], manual_context=context, defer_annotations=True)
    assert result.updated_details == 1
    item, = service.list_items(seed.session_id, session, manual_context=context)
    assert item.score_awarded == 6 and item.revision == 1
