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
    assert "timeout_override_seconds" not in (
        client.once_calls[0].get("extra_kwargs") or {}
    )
    assert client.repairing_calls == 0
