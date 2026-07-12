from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.jobs.store import JobStore
from backend.jobs.tagging_sync import run_tagging_sync_job
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.ai_tagging_service import AITaggingResult
from question_bank.services.question_service import QuestionService


def _analysis(*, confidence: float = 0.88) -> TagAnalysis:
    return TagAnalysis.from_dict(
        {
            "knowledge_points": ["整式运算"],
            "method_tags": ["整体思想"],
            "ability_tags": ["运算能力"],
            "math_model_tags": [],
            "difficulty": 3,
            "error_prone_points": ["符号错误"],
            "prerequisite_points": ["有理数运算"],
            "textbook_chapter": "七年级下册 第一章 整式的乘除",
            "teaching_stage": "期末复习",
            "suitable_student_level": "基础巩固",
            "reason": "考查整式运算。",
            "confidence": confidence,
        }
    )


def _complete() -> AITaggingResult:
    return AITaggingResult(
        ok=True,
        mock_mode=False,
        analysis=_analysis(),
        model_name="fake-tag-model",
        quality_status="complete",
    )


def _partial(error: str = "quality incomplete") -> AITaggingResult:
    return AITaggingResult(
        ok=True,
        mock_mode=False,
        analysis=_analysis(confidence=0.4),
        error=error,
        model_name="fake-tag-model",
        quality_status="partial",
        quality_notes=[error],
    )


class FakeAI:
    def __init__(self, results, *, on_call=None) -> None:
        self.results = dict(results)
        self.on_call = on_call
        self.calls: list[list[int]] = []

    def analyze_questions(self, contexts, **_kwargs):
        ids = sorted(int(item) for item in contexts)
        self.calls.append(ids)
        if self.on_call is not None:
            self.on_call()
        return {question_id: self.results[question_id] for question_id in ids}


def _seed(db_path: Path, count: int) -> list[int]:
    service = QuestionService(db_path)
    return [
        service.add_question(
            QuestionCreate(
                question_number=str(index),
                question_text=f"第{index}题测试题干",
                answer_text=str(index),
            )
        )
        for index in range(1, count + 1)
    ]


def _context(tmp_path: Path, payload: dict[str, object]) -> tuple[JobContext, JobStore]:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("tagging_sync", payload)
    assert store.mark_running(job.id)
    return JobContext(job.id, job.job_type, job.payload, store), store


def test_tagging_sync_saves_only_complete_results(tmp_path: Path) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    fake_ai = FakeAI({ids[0]: _complete(), ids[1]: _partial()})
    context, _store = _context(tmp_path, {"question_ids": ids})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
        batch_size=2,
    )

    assert result["outcome"] == "partial"
    assert result["successful_question_ids"] == [ids[0]]
    assert result["failed_question_ids"] == [ids[1]]
    assert result["failures"][0]["category"] == "quality"
    assert QuestionService(db_path).get_question(ids[0])["tags"]
    assert QuestionService(db_path).get_question(ids[1])["tags"] == []
    assert "fake-tag-model" not in json.dumps(result)


def test_tagging_sync_skips_complete_questions_and_retries_only_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    service = QuestionService(db_path)
    assert service.save_tag_analysis(
        ids[0], _analysis(), model_name="existing", resolve_skills=False
    )
    fake_ai = FakeAI({ids[1]: _complete()})
    context, _store = _context(tmp_path, {"question_ids": ids})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
        batch_size=1,
    )

    assert fake_ai.calls == [[ids[1]]]
    assert result["skipped_complete_count"] == 1
    assert result["tagged_count"] == 1
    assert result["outcome"] == "complete"


def test_tagging_sync_classifies_missing_and_deleted_questions(tmp_path: Path) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    service = QuestionService(db_path)
    assert service.delete_question(ids[0])
    fake_ai = FakeAI({})
    context, _store = _context(
        tmp_path, {"question_ids": [ids[0], 99999]}
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
    )

    assert fake_ai.calls == []
    assert result["failed_question_ids"] == [ids[0], 99999]
    assert {item["category"] for item in result["failures"]} == {"validation"}
    assert result["retryable"] is False


def test_tagging_sync_cancellation_during_batch_discards_batch_and_stops_next(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    context, store = _context(tmp_path, {"question_ids": ids})
    fake_ai = FakeAI(
        {ids[0]: _complete(), ids[1]: _complete()},
        on_call=lambda: store.request_cancel(context.job_id),
    )

    with pytest.raises(JobCancellationRequested):
        run_tagging_sync_job(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: fake_ai,
            batch_size=1,
        )

    assert fake_ai.calls == [[ids[0]]]
    assert QuestionService(db_path).get_question(ids[0])["tags"] == []
    assert QuestionService(db_path).get_question(ids[1])["tags"] == []


def test_tagging_sync_sanitizes_ai_failure_details(tmp_path: Path) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    raw_error = f"Timeout API key=super-secret at {tmp_path}"
    failed = AITaggingResult(
        ok=False,
        mock_mode=False,
        error=raw_error,
        model_name="fake-tag-model",
        quality_status="invalid",
    )
    fake_ai = FakeAI({ids[0]: failed})
    context, _store = _context(tmp_path, {"question_ids": ids})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["failures"][0]["category"] == "timeout"
    assert "super-secret" not in serialized
    assert str(tmp_path) not in serialized
