from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from PIL import Image

import grading_service
import llm_client
from ai_grader import AIGrader
from backend.llm import NullCallTraceSink, NullUsageSink
from request_pacer import RequestPacer
from scanner import ExamPaperGroup


class _SingleRequestClient:
    def __init__(self) -> None:
        self.once_calls: list[dict[str, object]] = []
        self.repairing_calls = 0

    def json_from_images_once(self, *_args, **kwargs):
        self.once_calls.append(dict(kwargs))
        return {
            "student_name": "Student 1",
            "total_score": 5,
            "student_score": 5,
            "needs_human_review": False,
            "grading_details": [
                {
                    "question_id": "Q1",
                    "score_awarded": 5,
                    "deduction_reason": "",
                    "confidence_score": 100,
                    "error_category": None,
                    "error_summary": None,
                    "secondary_errors": [],
                    "observed_answer": "complete solution",
                    "evidence_steps": ["valid step"],
                    "answer_is_blank_or_no_valid_work": False,
                }
            ],
        }

    def json_from_images_with_options(self, *_args, **_kwargs):
        self.repairing_calls += 1
        raise AssertionError("whole-paper grading must not use the model-repair path")


def _paper(tmp_path: Path) -> ExamPaperGroup:
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    Image.new("RGB", (20, 20), "white").save(front)
    Image.new("RGB", (20, 20), "white").save(back)
    return ExamPaperGroup(
        front_image=front,
        back_image=back,
        student_name="Student 1",
        student_id=1,
        source_label="001",
    )


def test_full_paper_grading_uses_one_model_request_and_local_json_repair_path(
    tmp_path: Path,
) -> None:
    rubric = tmp_path / "rubric.json"
    rubric.write_text(
        json.dumps(
            {
                "total_score": 5,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "calculation",
                        "response_mode": "short_answer_points",
                        "max_score": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = _SingleRequestClient()

    result = AIGrader(rubric, client).grade(_paper(tmp_path))

    assert result.student_score == 5
    assert len(client.once_calls) == 1
    assert callable(client.once_calls[0]["usage_callback"])
    assert client.once_calls[0]["extra_kwargs"] == {
        "timeout_override_seconds": 600
    }
    assert client.repairing_calls == 0


def test_evidence_atlas_user_prompt_uses_clear_chinese_without_changing_protocol(
    tmp_path: Path,
) -> None:
    rubric = tmp_path / "rubric.json"
    rubric.write_text(
        json.dumps(
            {
                "total_score": 5,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "calculation",
                        "response_mode": "process_required",
                        "max_score": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "tiles": [
            {
                "question_id": "Q1",
                "page_index": 0,
                "bbox": [10, 20, 30, 40],
            }
        ]
    }

    prompt = AIGrader(rubric, _SingleRequestClient())._build_atlas_user_prompt(
        "学生甲",
        manifest,
        reference_question_ids=["Q1"],
    )

    assert "已识别学生姓名：学生甲" in prompt
    assert "前 1 张图片依次是以下题目的标准答案原图：['Q1']" in prompt
    assert "最后一张图片是该学生的作答证据拼图" in prompt
    assert "只批改这张证据拼图中呈现的目标题目" in prompt
    assert "grading_details" in prompt
    assert json.dumps(manifest, ensure_ascii=False) in prompt
    assert "Recognized student name" not in prompt
    assert "The image provided is an evidence atlas" not in prompt
    assert "Return strict JSON only" not in prompt


def test_full_paper_timeout_reaches_sdk_without_changing_shared_grading_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rubric = tmp_path / "rubric.json"
    rubric.write_text(
        json.dumps(
            {
                "total_score": 5,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "calculation",
                        "response_mode": "short_answer_points",
                        "max_score": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    grading_payload = {
        "student_name": "Student 1",
        "total_score": 5,
        "student_score": 5,
        "needs_human_review": False,
        "grading_details": [
            {
                "question_id": "Q1",
                "score_awarded": 5,
                "deduction_reason": "",
                "confidence_score": 100,
                "error_category": None,
                "error_summary": None,
                "secondary_errors": [],
                "observed_answer": "complete solution",
                "evidence_steps": ["valid step"],
                "answer_is_blank_or_no_valid_work": False,
            }
        ],
    }
    responses = [grading_payload, {"ok": True}]
    sdk_calls: list[dict[str, object]] = []

    class FakeCreate:
        def create(self, **kwargs: object) -> object:
            sdk_calls.append(kwargs)
            payload = responses.pop(0)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=json.dumps(payload)),
                    )
                ],
                usage=None,
            )

    fake_sdk = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCreate()),
    )
    monkeypatch.setattr(llm_client, "_create_openai_client", lambda *_args: fake_sdk)
    monkeypatch.setattr("usage_logger.log_llm_usage", lambda _record: None)
    client = llm_client.LLMClient(
        llm_client.LLMSettings(
            api_key="test-key",
            base_url="https://example.invalid/v1",
            ocr_model="ocr-model",
            grading_model="grading-model",
            config_model="config-model",
        ),
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )

    result = AIGrader(rubric, client).grade(_paper(tmp_path))
    client.json_from_images_with_options(
        "hybrid",
        [b"not-an-image"],
        extra_kwargs={"omit_token_limit": True, "timeout": None},
    )

    assert result.student_score == 5
    assert [call["timeout"] for call in sdk_calls] == [600.0, 300.0]


def test_full_paper_transport_failure_is_not_retried_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingGrader:
        target_question_ids: list[str] = []

        def __init__(self) -> None:
            self.calls = 0

        def grade(self, _group, report=None):
            self.calls += 1
            raise openai.APIConnectionError(
                request=httpx.Request("POST", "https://example.invalid")
            )

    grader = FailingGrader()
    monkeypatch.delenv("AI_GRADING_FULL_PAPER_RETRIES", raising=False)
    monkeypatch.setattr(grading_service.time, "sleep", lambda _seconds: None)

    with pytest.raises(openai.APIConnectionError):
        grading_service._grade_one_paper_with_retries(
            grader,
            _paper(tmp_path),
            RequestPacer(1000, clock=lambda: 0.0, sleeper=lambda _seconds: None),
        )

    assert grader.calls == 1
