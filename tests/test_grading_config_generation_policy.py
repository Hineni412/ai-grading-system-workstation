from __future__ import annotations

import copy
from pathlib import Path
import base64
from types import SimpleNamespace

import pytest

from backend.llm.gateway import LLMGateway
from backend.llm.trace import NullCallTraceSink
import score_policy
import session_manager
import llm_client


def _scored_question(
    question_id: str,
    question_type: str,
    score: int,
    *,
    response_mode: str | None = None,
) -> dict:
    part = {
        "part_id": question_id,
        "part_score": score,
        "steps": [{"step_id": "S1", "step_score": score}],
    }
    if response_mode is not None:
        part["response_mode"] = response_mode
    return {
        "question_id": question_id,
        "question_type": question_type,
        "max_score": score,
        "parts": [part],
    }


def test_image_semantic_source_does_not_overwrite_ai_answer_with_corrupt_local_text() -> (
    None
):
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q9",
                    "question_type": "fill_blank",
                    "stem_summary": "一次函数实际应用",
                    "knowledge_id": "一次函数",
                    "knowledge_name": "一次函数实际应用",
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q9",
                    "canonical_answer": "y=48x+20",
                    "accepted_forms": ["y=48x+20"],
                }
            ]
        },
    }
    local_block = {
        "question_id": "Q9",
        "question_type": "fill_blank",
        "question_type_confirmed": True,
        "semantic_source": "images",
        "canonical_answer": "�=48�+20",
        "local_answer_trusted": True,
        "question_text": "乱码题干�",
    }

    session_manager._apply_local_question_facts(payload, [local_block])

    question = payload["rubric"]["questions"][0]
    answer = payload["answer_key"]["questions"][0]
    assert question["stem_summary"] == "一次函数实际应用"
    assert answer["canonical_answer"] == "y=48x+20"


def _single_question_payload(question_id: str, question_type: str = "choice") -> dict:
    return {
        "rubric": {
            "questions": [
                {
                    "question_id": question_id,
                    "question_type": question_type,
                    "knowledge_id": "K1",
                    "knowledge_name": "测试知识点",
                    "max_score": 1,
                    "parts": [
                        {
                            "part_id": question_id,
                            "part_score": 1,
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 1,
                                    "core_goal": "核对答案",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": question_id,
                    "canonical_answer": "A",
                    "accepted_forms": ["A"],
                }
            ]
        },
    }


def test_retry_failed_questions_preserves_successes_and_then_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _single_question_payload("Q1", "proof")
    existing["rubric"]["questions"][0]["parts"][0]["steps"][0]["core_goal"] = (
        "人工修改后保留"
    )
    existing["rubric"]["questions"].append(
        session_manager._placeholder_question_from_block(
            {"question_id": "Q2", "question_type": "choice", "text": "Choose B"}
        )
    )
    existing["answer_key"]["questions"].append(
        {"question_id": "Q2", "canonical_answer": "", "accepted_forms": [], "parts": []}
    )
    existing["meta"] = {
        "warnings": ["parallel generation failed for Q2: temporary upstream failure"],
        "failed_question_ids": ["Q2"],
        "failed_questions": [
            {
                "question_id": "Q2",
                "attempts": 3,
                "category": "transient_network",
                "error": "temporary upstream failure",
            }
        ],
    }

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _single_question_payload("Q2")
            return {
                "question_scores": [
                    {
                        "question_id": qid,
                        "max_score": 10,
                        "parts": [
                            {
                                "part_id": qid,
                                "part_score": 10,
                                "steps": [{"step_id": "S1", "step_score": 10}],
                            }
                        ],
                    }
                    for qid in ["Q1", "Q2"]
                ]
            }

    monkeypatch.setenv("AI_GRADING_CONFIG_RETRY_DELAYS", "0,0")
    monkeypatch.setattr(
        session_manager, "force_payload_total_score", lambda *_args, **_kwargs: None
    )
    client = FakeClient()
    blocks = [
        {"question_id": "Q1", "question_type": "proof", "text": "Prove A"},
        {"question_id": "Q2", "question_type": "choice", "text": "Choose B"},
    ]

    payload = session_manager.retry_failed_grading_config_questions(
        existing, blocks, "", client
    )

    questions = {
        question["question_id"]: question for question in payload["rubric"]["questions"]
    }
    assert client.calls == 1
    assert not any(key.startswith("knowledge") for key in questions["Q2"])
    assert payload["meta"]["failed_question_ids"] == []
    assert payload["meta"]["score_allocation_mode"] == "local_step_weighted"
    assert not any(
        "Q2 placeholder added" in warning for warning in payload["meta"]["warnings"]
    )
    assert questions["Q1"]["parts"][0]["steps"][0]["core_goal"] == "人工修改后保留"


def test_word_whole_generation_is_one_request_and_uses_shared_postprocessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scores = [17, 17, 17, 17, 17, 15]
    payload = {
        "rubric": {
            "questions": [
                _scored_question(
                    f"Q{i}",
                    "comprehensive",
                    score,
                    response_mode="short_answer_points",
                )
                for i, score in enumerate(scores, start=1)
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": f"Q{i}",
                    "canonical_answer": "answer",
                    "accepted_forms": [],
                }
                for i in range(1, 7)
            ]
        },
    }

    class FakeClient:
        def __init__(self) -> None:
            self.once_calls = 0

        def json_from_text_once(self, prompt, **_kwargs):
            self.once_calls += 1
            assert "paper text" in prompt
            assert "knowledge_name" not in prompt
            return payload

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError(
                "whole Word mode must use the strict single-request method"
            )

    monkeypatch.setattr(session_manager, "_needs_objective_repair", lambda *_args: True)
    client = FakeClient()

    result = session_manager.generate_grading_config_from_docx_text(
        "paper text", client
    )

    assert client.once_calls == 1
    assert result["rubric"]["total_score"] == 100
    assert result["meta"]["generation_mode"] == "whole_word_text_single_request"
    assert (
        result["meta"]["score_allocation_mode"] == "single_request_local_normalization"
    )


def test_pdf_whole_generation_is_one_visual_request_without_extracted_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scores = [17, 17, 17, 17, 17, 15]
    payload = {
        "rubric": {
            "questions": [
                _scored_question(
                    f"Q{i}",
                    "comprehensive",
                    score,
                    response_mode="short_answer_points",
                )
                for i, score in enumerate(scores, start=1)
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": f"Q{i}",
                    "canonical_answer": "answer",
                    "accepted_forms": [],
                }
                for i in range(1, 7)
            ]
        },
    }

    class FakeClient:
        def __init__(self) -> None:
            self.once_calls = 0

        def json_from_images_once(self, prompt, images, **_kwargs):
            self.once_calls += 1
            assert images == [b"page-1", b"page-2"]
            assert "CORRUPTED_PDF_TEXT" not in prompt
            assert "图片是唯一权威内容来源" in prompt
            return payload

        def json_from_images_with_options(self, *_args, **_kwargs):
            raise AssertionError(
                "whole PDF mode must use the strict single-request method"
            )

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("whole PDF mode must never send extracted PDF text")

    monkeypatch.setattr(session_manager, "_needs_objective_repair", lambda *_args: True)
    client = FakeClient()

    result = session_manager.generate_grading_config_from_images(
        [b"page-1", b"page-2"],
        "CORRUPTED_PDF_TEXT",
        client,
    )

    assert client.once_calls == 1
    assert result["rubric"]["total_score"] == 100
    assert result["meta"]["generation_mode"] == "whole_pdf_visual_single_request"
    assert (
        result["meta"]["score_allocation_mode"] == "single_request_local_normalization"
    )
