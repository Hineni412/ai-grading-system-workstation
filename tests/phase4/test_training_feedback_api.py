from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

from backend.training_assessment import (
    AssessmentReviewConflict,
    AssessmentUsage,
    TrainingPaperOutcome,
)


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


SUBMISSION_ID = "c" * 64


class FakeAssessmentModule:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[tuple[object, ...]] = []
        self.outcome = TrainingPaperOutcome(
            run_id="a" * 64,
            submission_id=SUBMISSION_ID,
            submission_revision=1,
            status="partial",
            request_count=1,
            expected_question_count=1,
            expected_point_count=2,
            model_name="synthetic-model",
            usage=AssessmentUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
            latency_ms=50,
            issue_codes=(),
            error_code=None,
            questions=(
                {
                    "task_item_code": "P4-SYN-Q01",
                    "item_order": 1,
                    "status": "review_required",
                    "met_count": 1,
                    "not_met_count": 0,
                    "uncertain_count": 1,
                    "unreadable_count": 0,
                    "total_count": 2,
                    "review_status": "pending",
                    "review_points": [
                        {
                            "point_id": "p1",
                            "content": "写出关键步骤",
                            "state": "met",
                            "evidence": "合成依据",
                            "teacher_locked": False,
                            "teacher_reason": None,
                            "actor_ref": None,
                            "lock_revision": 0,
                        },
                        {
                            "point_id": "p2",
                            "content": "得到结论",
                            "state": "uncertain",
                            "evidence": "图像不清",
                            "teacher_locked": False,
                            "teacher_reason": None,
                            "actor_ref": None,
                            "lock_revision": 0,
                        },
                    ],
                },
            ),
            review_revision=1,
            control_state="active",
            workflow_status="review_required",
            action_message="请复核不确定判定点。",
            attempts=(),
        )
        self.feedback = {
            "schema_version": "training-feedback-v1",
            "feedback_id": "f" * 64,
            "submission_id": SUBMISSION_ID,
            "submission_revision": 1,
            "source_review_revision": 1,
            "status": "partial",
            "student": {
                "student_id": "SYN-S01",
                "student_code": "S01",
                "student_name": "合成学生",
                "class_id": "SYN-C01",
            },
            "summary": {
                "published_question_count": 1,
                "ready_question_count": 1,
                "total_question_count": 1,
                "pending_outbox_count": 0,
                "message": "已形成逐题训练证据。",
            },
            "questions": [],
            "mastery_changes": [],
            "next_round": {
                "status": "draft",
                "draft_id": "d" * 64,
                "message": "下一轮草稿等待教师确认。",
                "changes": [],
            },
            "timeline": [],
            "safety": {
                "is_exam_score": False,
                "changes_v1": False,
                "auto_paper_created": False,
                "auto_printed": False,
            },
            "evidence_version": "e" * 64,
        }

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    def assess(self, submission_id, expected_revision):
        self._raise()
        self.calls.append(("assess", submission_id, expected_revision))
        return self.outcome

    def get_outcome(self, submission_id, revision):
        self._raise()
        self.calls.append(("get", submission_id, revision))
        return self.outcome

    def review_point(self, submission_id, revision, command):
        self._raise()
        self.calls.append(("review", submission_id, revision, command))
        return self.outcome

    def retry_failed(self, submission_id, revision, command):
        self._raise()
        self.calls.append(("retry", submission_id, revision, command))
        return self.outcome

    def pause(self, submission_id, revision, command):
        return self.retry_failed(submission_id, revision, command)

    def resume(self, submission_id, revision, command):
        return self.retry_failed(submission_id, revision, command)

    def cancel(self, submission_id, revision, command):
        return self.retry_failed(submission_id, revision, command)

    def recover(self, submission_id, revision, command):
        return self.retry_failed(submission_id, revision, command)

    def sync_evidence(self, submission_id, revision, command):
        self._raise()
        self.calls.append(("evidence", submission_id, revision, command))
        return self.feedback

    def get_feedback(self, submission_id, revision):
        self._raise()
        self.calls.append(("feedback", submission_id, revision))
        return self.feedback

    def replay_evidence_outbox(self, max_items):
        self._raise()
        self.calls.append(("replay", max_items))
        return {
            "examined_count": 1,
            "delivered_count": 1,
            "failed_count": 0,
            "feedbacks": [self.feedback],
        }


@pytest.fixture()
def feedback_client() -> tuple[TestClient, FakeAssessmentModule]:
    from backend.api.app import create_app
    from backend.api.dependencies import get_training_assessment_module

    module = FakeAssessmentModule()
    app = create_app()
    app.dependency_overrides[get_training_assessment_module] = lambda: module
    return TestClient(app), module


def test_assessment_review_evidence_feedback_and_replay_contract(
    feedback_client,
) -> None:
    client, module = feedback_client
    started = client.post(
        f"/api/training/submissions/{SUBMISSION_ID}/assessment",
        json={"expected_revision": 1},
    )
    assert started.status_code == 200
    assert started.json()["workflow_status"] == "review_required"
    assert started.headers["cache-control"] == "no-store"
    assert "path" not in started.text.casefold()

    loaded = client.get(
        f"/api/training/submissions/{SUBMISSION_ID}/assessment",
        params={"submission_revision": 1},
    )
    assert loaded.status_code == 200

    reviewed = client.post(
        f"/api/training/submissions/{SUBMISSION_ID}/assessment/reviews",
        json={
            "operation_token": "1" * 32,
            "submission_revision": 1,
            "expected_review_revision": 1,
            "task_item_code": "P4-SYN-Q01",
            "point_id": "p2",
            "final_state": "not_met",
            "teacher_evidence": "教师核对原图",
            "teacher_reason": "确认该点未达成",
        },
    )
    assert reviewed.status_code == 200
    assert module.calls[-1][3].actor_ref == "local_teacher"

    retried = client.post(
        f"/api/training/submissions/{SUBMISSION_ID}/assessment/actions",
        json={
            "operation_token": "2" * 32,
            "submission_revision": 1,
            "expected_review_revision": 1,
            "action": "retry",
            "reason": "教师确认后显式重试",
        },
    )
    assert retried.status_code == 200
    assert module.calls[-1][0] == "retry"

    published = client.post(
        f"/api/training/submissions/{SUBMISSION_ID}/evidence",
        json={
            "operation_token": "3" * 32,
            "submission_revision": 1,
            "expected_review_revision": 1,
            "action": "publish",
            "reason": "教师确认发布已完成题目",
        },
    )
    assert published.status_code == 200
    assert published.headers["cache-control"] == "no-store"
    assert published.json()["next_round"]["status"] == "draft"
    assert published.json()["safety"]["auto_paper_created"] is False

    feedback = client.get(
        f"/api/training/submissions/{SUBMISSION_ID}/feedback",
        params={"submission_revision": 1},
    )
    assert feedback.status_code == 200

    replayed = client.post(
        "/api/training/evidence/replay",
        json={"max_items": 20},
    )
    assert replayed.status_code == 200
    assert replayed.json()["delivered_count"] == 1


def test_assessment_review_conflict_is_public_and_safe(
    feedback_client,
) -> None:
    client, module = feedback_client
    module.error = AssessmentReviewConflict(1, 2)
    response = client.get(
        f"/api/training/submissions/{SUBMISSION_ID}/assessment",
        params={"submission_revision": 1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "training_assessment_review_conflict"
    )
    assert response.json()["error"]["details"] == {"current_revision": 2}
    assert "path" not in response.text.casefold()
