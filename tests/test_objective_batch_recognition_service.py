from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from backend.scan_grading.objective_batch_recognition_service import (
    ObjectivePaperEntry,
    crop_objective_region,
    run_objective_batch_recognition,
)
from backend.scan_grading.scanner import ExamPaperGroup
from backend.repositories.grading_database import open_grading_repositories


class FakeBatchClient:
    def __init__(
        self,
        confidence: float = 0.99,
        need_review: bool = False,
        answer: str = "A",
        review_reason: str | None = None,
        normalized_answer: str | None = None,
        raw_answer: str | None = None,
        score: float | None = None,
        error_category: str = "",
    ) -> None:
        self.confidence = confidence
        self.need_review = need_review
        self.answer = answer
        self.review_reason = review_reason
        self.normalized_answer = normalized_answer
        self.raw_answer = raw_answer
        self.score = score
        self.error_category = error_category
        self.calls: list[dict[str, Any]] = []

    def json_from_images(
        self,
        prompt: str,
        images: list[bytes],
        model: str | None = None,
        usage_callback: Any = None,
        allow_gateway_retry: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "images": images,
                "model": model,
                "allow_gateway_retry": allow_gateway_retry,
            }
        )
        manifest = json.loads(prompt.split("BATCH_MANIFEST_JSON:", 1)[1].strip())
        if usage_callback is not None:
            usage_callback(
                type(
                    "Completion",
                    (),
                    {
                        "usage": type(
                            "Usage",
                            (),
                            {
                                "prompt_tokens": 100,
                                "completion_tokens": 10,
                                "total_tokens": 110,
                                "prompt_tokens_details": type(
                                    "Details", (), {"cached_tokens": 20}
                                )(),
                            },
                        )()
                    },
                )(),
                {"model": model},
            )
        answers = []
        scoring = json.loads(
            prompt.rsplit("SCORING_CONTEXT_JSON:", 1)[1].split(
                "BATCH_MANIFEST_JSON:", 1
            )[0]
        )
        max_scores = {item["question_id"]: item["max_score"] for item in scoring}
        for question_id in manifest["target_question_ids"]:
            question_type = manifest["question_types"][question_id]
            score = self.score if self.score is not None else max_scores[question_id]
            answers.append(
                {
                    "question_id": question_id,
                    "question_type": question_type,
                    "recognized_answer": self.answer,
                    "raw_answer": self.answer
                    if self.raw_answer is None
                    else self.raw_answer,
                    "normalized_answer": self.normalized_answer
                    if self.normalized_answer is not None
                    else self.answer,
                    "confidence": self.confidence,
                    "need_review": self.need_review,
                    "review_reason": self.review_reason
                    if self.review_reason is not None
                    else ("unclear" if self.need_review else ""),
                    "score_awarded": score,
                    "deduction_reason": "模型判定答案不符合评分要求。"
                    if score == 0
                    else "",
                    "error_category": self.error_category,
                    "answer_evidence": "模型读取到的最终有效作答。",
                    "answer_state": "uncertain"
                    if self.need_review
                    else (
                        "blank" if self.answer.casefold() in {"", "blank"} else "clear"
                    ),
                },
            )
        return {
            "paper_key": manifest["paper_key"],
            "student_id": manifest["student_id"],
            "answers": answers,
        }


class SlowBatchClient(FakeBatchClient):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2)

    def json_from_images(
        self,
        prompt: str,
        images: list[bytes],
        model: str | None = None,
        usage_callback: Any = None,
        allow_gateway_retry: bool = False,
    ) -> dict[str, Any]:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            try:
                self.barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return super().json_from_images(
                prompt,
                images,
                model=model,
                usage_callback=usage_callback,
                allow_gateway_retry=allow_gateway_retry,
            )
        finally:
            with self.lock:
                self.active -= 1


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            self.calls += 1


def test_crop_objective_region_scales_template_coordinates_to_scan_size(
    tmp_path: Path,
) -> None:
    front_image = tmp_path / "front.jpg"
    Image.new("RGB", (1768, 1224), "white").save(front_image)
    entry = ObjectivePaperEntry(
        paper_key="paper-1",
        student_id=1,
        student_name="student",
        group=ExamPaperGroup(
            front_image=front_image, back_image=None, student_name="student"
        ),
    )
    regions = [
        {
            "page": "front",
            "mapped_question_id": "Q10(2)",
            "x": 1504,
            "y": 867,
            "w": 687,
            "h": 453,
            "source_image_width": 2831,
            "source_image_height": 1960,
        }
    ]

    crop_path, bbox = crop_objective_region(
        entry=entry,
        question_id="Q10(2)",
        regions=regions,
        output_root=tmp_path / "crops",
    )

    assert crop_path.exists()
    assert bbox == {"x": 939, "y": 541, "w": 429, "h": 283}


def _save(path: Path) -> None:
    Image.new("RGB", (260, 180), color=(255, 255, 255)).save(path, format="JPEG")


def _groups(tmp_path: Path, count: int) -> list[ExamPaperGroup]:
    groups = []
    for index in range(1, count + 1):
        front = tmp_path / f"front_{index}.jpg"
        back = tmp_path / f"back_{index}.jpg"
        _save(front)
        _save(back)
        groups.append(
            ExamPaperGroup(
                front, back, f"Student {index}", index, source_label=f"scan_{index}"
            )
        )
    return groups


def test_objective_low_confidence_goes_to_review_without_a_second_paid_request(
    tmp_path: Path,
) -> None:
    primary = FakeBatchClient(
        confidence=0.50,
        need_review=False,
        answer="A",
        review_reason="low confidence cursive A",
    )
    fallback = FakeBatchClient(
        confidence=0.92, need_review=False, answer="A", review_reason=""
    )

    result = run_objective_batch_recognition(
        session_id=13,
        paper_groups=_groups(tmp_path, 1),
        answer_regions=[
            {
                "page": "front",
                "mapped_question_id": "Q7",
                "x": 10,
                "y": 10,
                "w": 120,
                "h": 80,
                "is_confirmed": True,
            }
        ],
        rubric={
            "questions": [
                {"question_id": "Q7", "question_type": "choice", "max_score": 8}
            ]
        },
        answer_key={"questions": [{"question_id": "Q7", "standard_answer": "A"}]},
        output_root=tmp_path / "out",
        recognition_client=primary,
        fallback_recognition_client=fallback,
        fallback_model="pro-model",
    )

    detail = next(iter(result.details_by_paper_key.values()))[0]
    metadata = next(iter(result.metadata_by_paper_key.values()))[0]
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 0
    assert detail.score_awarded == 8
    assert metadata["source"] == "objective_paper_recognition"
    assert metadata["need_review"] is True
    assert len(result.review_items) == 1


def test_hybrid_model_scores_survive_storage_and_teacher_confirmation(
    tmp_path: Path,
) -> None:
    import sqlite3
    
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.papers import PaperRepositoryGateway
    from backend.repositories.results import ResultRepositoryGateway
    from backend.repositories.review import ReviewRepositoryGateway
    from backend.scan_grading.ai_batch_grading_service import run_ai_batch_grading

    database = tmp_path / "scores.db"
    db = open_grading_repositories(database)
    db.initialize()
    session_id = db.sessions.create_grading_session("Test AI scores", "", "")
    with sqlite3.connect(database) as conn:
        student_id = conn.execute(
            "INSERT INTO students (student_code,name) VALUES ('test-1','Student 1')"
        ).lastrowid
    groups = _groups(tmp_path, 1)
    groups[0].student_id = student_id
    client = FakeBatchClient(answer="x>0", score=8, need_review=True, confidence=0.95)
    rubric = {
        "total_score": 8,
        "questions": [
            {"question_id": "Q1", "question_type": "fill_blank", "max_score": 8}
        ],
    }
    run = run_ai_batch_grading(
        session_id=session_id,
        paper_groups=groups,
        answer_regions=[
            {
                "page": "front",
                "mapped_question_id": "Q1",
                "x": 10,
                "y": 10,
                "w": 120,
                "h": 80,
            }
        ],
        rubric=rubric,
        answer_key={"questions": [{"question_id": "Q1", "canonical_answer": "(0,+∞)"}]},
        llm_client=client,
        grading_model="fake-grading-model",
        output_root=tmp_path / "out",
    )
    result = next(iter(run.results_by_paper_key.values()))
    assert len(client.calls) == 1
    assert run.fallback_items == []
    assert result.grading_details[0].score_awarded == 8
    assert result.needs_human_review is True
    sessions = SQLiteConnectionFactory(database)
    results = ResultRepositoryGateway(sessions, db_path=database)
    papers = PaperRepositoryGateway(sessions, results=results)
    paper_id = papers.create_exam_paper(
        session_id,
        str(groups[0].front_image),
        str(groups[0].back_image),
        "Student 1",
        student_id,
        "matched",
        "pending",
    )
    result_id = results.publish_session_result_if_current_assignment(
        session_id, student_id, paper_id, result, scan_batch_id="test-batch"
    )
    # Open a fresh repository to verify persisted scores and evidence.
    fresh = ResultRepositoryGateway(SQLiteConnectionFactory(database))
    detail = fresh.get_result_details(result_id)[0]
    assert detail["score_awarded"] == 8
    stored = fresh.get_student_result_for_retry(session_id, student_id)
    assert stored["raw_json"]["detail_metadata"]["Q1"]["model_score_awarded"] == 8
    assert stored["raw_json"]["detail_metadata"]["Q1"]["need_review"] is True
    with sqlite3.connect(database) as conn:
        detail_id = conn.execute(
            "SELECT id FROM session_details WHERE result_id = ? AND question_id = ?",
            (result_id, "Q1"),
        ).fetchone()[0]
    ReviewRepositoryGateway(sessions).confirm_teacher_score_lock(
        session_id=session_id,
        scan_batch_id="test-batch",
        student_id=student_id,
        question_id="Q1",
        score_awarded=0,
        max_score=8,
        deduction_reason="教师确认",
        source_target_type="session_detail",
        source_target_id=detail_id,
        expected_revision=0,
    )
    result_id = results.publish_session_result_if_current_assignment(
        session_id, student_id, paper_id, result, scan_batch_id="test-batch"
    )
    saved = fresh.get_result_details(result_id)[0]
    assert saved["score_awarded"] == 0
    assert saved["ai_score_awarded"] == 8
    assert fresh.get_session_results(session_id)[0]["student_score"] == 0


def test_hybrid_missing_model_score_remains_ungraded_and_can_be_targeted(
    tmp_path: Path,
) -> None:
    from backend.scan_grading.ai_batch_grading_service import run_ai_batch_grading

    class MissingOneScore(FakeBatchClient):
        def json_from_images(self, *args, **kwargs):
            response = super().json_from_images(*args, **kwargs)
            for item in response["answers"]:
                if item["question_id"] == "Q2":
                    item.pop("score_awarded")
            return response

    groups = _groups(tmp_path, 1)
    client = MissingOneScore()
    rubric = {
        "total_score": 16,
        "questions": [
            {"question_id": qid, "question_type": "choice", "max_score": 8}
            for qid in ("Q1", "Q2")
        ],
    }
    kwargs = dict(
        session_id=1,
        paper_groups=groups,
        rubric=rubric,
        answer_key={
            "questions": [
                {"question_id": qid, "canonical_answer": "A"} for qid in ("Q1", "Q2")
            ]
        },
        answer_regions=[
            {
                "page": "front",
                "mapped_question_id": qid,
                "x": 10,
                "y": 10 + i * 40,
                "w": 120,
                "h": 30,
            }
            for i, qid in enumerate(("Q1", "Q2"))
        ],
        grading_model="fake",
        output_root=tmp_path / "out",
    )
    run = run_ai_batch_grading(**kwargs, llm_client=client)
    result = next(iter(run.results_by_paper_key.values()))
    assert [(d.question_id, d.score_awarded) for d in result.grading_details] == [
        ("Q1", 8)
    ]
    assert result.raw_json["grading_completeness"]["missing_question_ids"] == ["Q2"]
    assert result.raw_json["detail_metadata"]["Q2"]["score_status"] == "ungraded"
    assert run.fallback_items[0]["question_id"] == "Q2"
    assert len(client.calls) == 1
    retry = FakeBatchClient(score=0, answer="D")
    run = run_ai_batch_grading(
        **kwargs,
        llm_client=retry,
        target_questions_by_student={groups[0].student_id: {"Q2"}},
    )
    retried = next(iter(run.results_by_paper_key.values()))
    assert [(d.question_id, d.score_awarded) for d in retried.grading_details] == [
        ("Q2", 0)
    ]
    assert len(retry.calls) == 1
    context = json.loads(
        retry.calls[0]["prompt"]
        .rsplit("SCORING_CONTEXT_JSON:", 1)[1]
        .split("BATCH_MANIFEST_JSON:", 1)[0]
    )
    assert [item["question_id"] for item in context] == ["Q2"]


def test_completely_unreadable_objective_answer_stays_unscored() -> None:
    from backend.scan_grading.objective_batch_recognition_service import (
        ObjectiveQuestionSpec,
        validate_objective_paper_response,
    )

    accepted, review = validate_objective_paper_response(
        response={
            "paper_key": "p",
            "student_id": 1,
            "answers": [
                {
                    "question_id": "Q1",
                    "recognized_answer": "",
                    "score_awarded": None,
                    "confidence": 0.1,
                    "answer_state": "uncertain",
                    "need_review": True,
                }
            ],
        },
        manifest={"paper_key": "p", "student_id": 1, "target_question_ids": ["Q1"]},
        specs=[ObjectiveQuestionSpec("Q1", "choice", "C", 8, {})],
        min_confidence=0.8,
    )

    assert accepted == []
    assert len(review) == 1
    assert review[0]["reason"] == "missing_model_score"


def _uncertain_paper_fill_blank_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "question_id": "Q1",
        "question_type": "fill_blank",
        "raw_answer": "x≤5",
        "recognized_answer": "x≤5",
        "confidence": 0.5,
        "need_review": True,
        "review_reason": "unclear handwriting",
        "answer_state": "uncertain",
        "score_awarded": 0,
        "deduction_reason": "不等号方向与参考答案不一致",
        "candidate_readings": [
            {"answer": "x≤5", "score_awarded": 0},
            {"answer": "x<5", "score_awarded": 0},
        ],
    }
    item.update(overrides)
    return item


def _validate_paper_item(
    item: dict[str, Any], spec: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from backend.scan_grading.objective_batch_recognition_service import validate_objective_paper_response

    return validate_objective_paper_response(
        response={
            "paper_key": "p",
            "student_id": 1,
            "answers": [{"question_id": "Q1", **item}],
        },
        manifest={"paper_key": "p", "student_id": 1, "target_question_ids": ["Q1"]},
        specs=[spec],
        min_confidence=0.85,
    )


def test_same_score_candidate_readings_waive_objective_review() -> None:
    from backend.scan_grading.grading_completeness import details_require_review
    from backend.scan_grading.objective_batch_recognition_service import ObjectiveQuestionSpec

    spec = ObjectiveQuestionSpec("Q1", "fill_blank", "x≤5", 3, {})
    accepted, review = _validate_paper_item(_uncertain_paper_fill_blank_item(), spec)

    assert review == []
    assert len(accepted) == 1
    detail = accepted[0]["detail"]
    metadata = accepted[0]["metadata"]
    assert metadata["need_review"] is False
    assert metadata["auto_scored"] is True
    assert metadata["review_reason"] == ""
    assert metadata["review_waiver"] == "all_readings_same_score"
    assert metadata["recognition_confidence"] == 0.5
    assert metadata["waived_review_reason"] == "unclear handwriting"
    assert metadata["candidate_readings"] == [
        {"answer": "x≤5", "score_awarded": 0},
        {"answer": "x<5", "score_awarded": 0},
    ]
    assert detail.confidence_score == 100
    assert detail.error_category != "需复核"
    assert detail.error_summary != "unclear handwriting"
    assert (
        details_require_review([detail], {"detail_metadata": {"Q1": metadata}}) is False
    )


def test_different_score_candidate_readings_still_need_review() -> None:
    from backend.scan_grading.objective_batch_recognition_service import ObjectiveQuestionSpec

    spec = ObjectiveQuestionSpec("Q1", "fill_blank", "x≤5", 3, {})
    item = _uncertain_paper_fill_blank_item(
        candidate_readings=[
            {"answer": "x≤5", "score_awarded": 0},
            {"answer": "x<5", "score_awarded": 3},
        ],
    )
    accepted, review = _validate_paper_item(item, spec)

    assert review == []
    assert len(accepted) == 1
    metadata = accepted[0]["metadata"]
    assert metadata["need_review"] is True
    assert "review_waiver" not in metadata
    assert "candidate_readings" not in metadata


def test_unreadable_answer_source_file_stops_objective_recognition(
    tmp_path, monkeypatch
) -> None:
    """标准答案文件损坏时必须中止识别，不能按空答案把客观题判成缺答。"""
    import backend.scan_grading.objective_answer_loader as objective_answer_loader
    from backend.scan_grading.objective_answer_loader import load_objective_answer_sources
    from backend.scan_grading.objective_batch_recognition_service import (
        build_objective_question_specs,
    )

    session_dir = tmp_path / "templates" / "session_9"
    session_dir.mkdir(parents=True)
    (session_dir / "answer_key.json").write_text(
        "{broken", encoding="utf-8"
    )
    fake_pm = type(
        "_PM",
        (),
        {
            "templates_dir": tmp_path / "templates",
            "project_root": tmp_path / "missing_project_root",
        },
    )()
    monkeypatch.setattr(
        objective_answer_loader, "get_path_manager", lambda: fake_pm
    )

    with pytest.raises(ValueError, match="answer_key.json"):
        load_objective_answer_sources("9")
    with pytest.raises(ValueError, match="answer_key.json"):
        build_objective_question_specs(
            "9",
            {"questions": [{"question_id": "Q1", "question_type": "choice"}]},
            {"questions": []},
        )


@pytest.mark.parametrize("answer,expected_score", [("3", 4), ("4", 4), ("100", 4), ("2", 0), ("2.5", 0)])
def test_open_fill_answer_receives_all_or_no_marks_from_conditions_payload(
    tmp_path: Path, answer: str, expected_score: int,
) -> None:
    from tests.test_config_generation_contract import _open_generated_config
    from backend.config_generation.normalization import normalize_new_generated_config_payload

    class ConditionsClient(FakeBatchClient):
        def json_from_images(self, prompt, images, **kwargs):
            context = json.loads(prompt.rsplit("SCORING_CONTEXT_JSON:", 1)[1].split("BATCH_MANIFEST_JSON:", 1)[0])[0]
            step = context["rubric"]["parts"][0]["steps"][0]
            assert step["answer_kind"] == "conditions"
            assert step["core_goal"] == "给出一个大于 2 的整数"
            assert step["required_elements"] == ["答案是整数且大于 2"]
            value = float(self.answer)
            self.score = context["max_score"] if value.is_integer() and value > 2 else 0
            return super().json_from_images(prompt, images, **kwargs)

    payload = _open_generated_config()
    normalize_new_generated_config_payload(payload)
    payload["rubric"]["questions"][0]["max_score"] = 4
    payload["rubric"]["questions"][0]["parts"][0]["part_score"] = 4
    payload["rubric"]["questions"][0]["parts"][0]["steps"][0]["step_score"] = 4
    client = ConditionsClient(answer=answer)
    result = run_objective_batch_recognition(session_id="TEST-open-fill", paper_groups=_groups(tmp_path, 1),
        answer_regions=[{"page": "front", "mapped_question_id": "Q5", "x": 10, "y": 10, "w": 120, "h": 80}],
        rubric=payload["rubric"], answer_key=payload["answer_key"], output_root=tmp_path / "TEST-open-out",
        recognition_client=client, recognition_model="TEST-conditions-model")
    details = next(iter(result.details_by_paper_key.values()))
    assert len(client.calls) == 1
    assert result.review_items == []
    assert details[0].score_awarded == expected_score
    assert details[0].question_id == "Q5"
