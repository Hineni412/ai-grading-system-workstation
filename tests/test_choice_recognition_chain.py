from __future__ import annotations

import math
from types import SimpleNamespace

import api_profiles
import backend.llm as backend_llm
import openai
import usage_logger
from PIL import Image

from choice_recognition_chain import (
    recognize_choice_answer,
    score_choice_by_program,
)


def test_score_choice_by_program_matches_any_accepted_form() -> None:
    result = score_choice_by_program(
        selected="C",
        standard_answer=["B", "C"],
        max_score=8,
        confidence=0.95,
    )

    assert result["is_correct"] is True
    assert result["score"] == 8
    assert result["auto_scored"] is True
    assert result["need_review"] is False


def test_explicit_empty_choice_is_blank_but_missing_or_unclear_answer_needs_review() -> None:
    for answer in ("", "blank", "   "):
        result = score_choice_by_program(answer, "A", 5, 1.0)
        assert result["score"] == 0
        assert result["need_review"] is False
    for answer in (None, "unclear", "multiple"):
        assert score_choice_by_program(answer, "A", 5, 1.0)["need_review"] is True


def test_choice_recognition_routes_existing_request_through_recognition_gateway(
    monkeypatch,
    tmp_path,
) -> None:
    provider_calls: list[dict[str, object]] = []
    adapter_init_calls: list[dict[str, object]] = []
    adapter_calls: list[dict[str, object]] = []
    workspace_usage_writes: list[dict[str, object]] = []
    policy_profile = {
        "llm_recognition_timeout_seconds": 37,
        "llm_recognition_max_retries": 0,
        "llm_recognition_requests_per_minute": 5000,
    }
    config = {
        "enabled": True,
        "model": "fake-choice-model",
        "api_key": "fake-choice-key",
        "base_url": "https://provider.invalid/v1",
        "timeout": 999,
        "temperature": 0.2,
        "max_tokens": 123,
        "thinking_type": "enabled",
        "policy_profile": policy_profile,
    }
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"selected":"C","confidence":0.96,'
                        '"need_review":false,"review_reason":""}'
                    )
                )
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )

    def fake_provider_create(**kwargs: object) -> object:
        provider_calls.append(kwargs)
        return completion

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_provider_create)
        )
    )
    real_adapter_class = backend_llm.LLMProtocolAdapter

    class InjectedProtocolAdapter:
        def __init__(
            self,
            api_key: str,
            base_url: str,
            policy_profile=None,
        ) -> None:
            adapter_init_calls.append(
                {
                    "api_key": api_key,
                    "base_url": base_url,
                    "policy_profile": policy_profile,
                }
            )
            self._adapter = real_adapter_class(
                api_key,
                base_url,
                policy_profile=policy_profile,
                client=fake_client,
                usage_sink_factory=backend_llm.NullUsageSink,
                trace_sink_factory=backend_llm.NullCallTraceSink,
            )

        def chat_completions(self, **kwargs: object) -> object:
            adapter_calls.append(kwargs)
            return self._adapter.chat_completions(**kwargs)

    def forbid_direct_sdk(*args: object, **kwargs: object) -> object:
        raise AssertionError("direct OpenAI SDK boundary is still in use")

    monkeypatch.setattr(
        "api_profiles.get_objective_api_config",
        lambda: dict(config),
    )
    monkeypatch.setattr(
        backend_llm,
        "LLMProtocolAdapter",
        InjectedProtocolAdapter,
    )
    monkeypatch.setattr(openai, "OpenAI", forbid_direct_sdk)
    monkeypatch.setattr(
        usage_logger,
        "log_llm_usage",
        lambda record: workspace_usage_writes.append(record),
    )
    image_path = tmp_path / "choice.jpg"
    Image.new("RGB", (32, 24), "white").save(image_path, format="JPEG")

    result = recognize_choice_answer(
        image_path=image_path,
        question_id="choice-2",
        standard_answer="C",
        max_score=8,
        session_id="fake-session",
        student_id="fake-student",
    )

    assert provider_calls, result["error_type"]
    assert adapter_init_calls == [
        {
            "api_key": "fake-choice-key",
            "base_url": "https://provider.invalid/v1",
            "policy_profile": policy_profile,
        }
    ]
    assert len(adapter_calls) == 1
    assert (
        adapter_calls[0]["request_kind"]
        is backend_llm.LLMRequestKind.RECOGNITION
    )
    assert adapter_calls[0]["model"] == "fake-choice-model"
    assert set(adapter_calls[0]["kwargs"]) == {
        "messages",
        "temperature",
        "max_tokens",
        "response_format",
        "extra_body",
    }

    provider_kwargs = provider_calls[0]
    assert set(provider_kwargs) == {
        "messages",
        "temperature",
        "max_tokens",
        "response_format",
        "extra_body",
        "model",
        "timeout",
    }
    assert provider_kwargs["model"] == "fake-choice-model"
    assert math.isfinite(provider_kwargs["timeout"])
    assert provider_kwargs["timeout"] == 37.0
    assert provider_kwargs["temperature"] == 0.2
    assert provider_kwargs["max_tokens"] == 123
    assert provider_kwargs["response_format"] == {"type": "json_object"}
    assert provider_kwargs["extra_body"] == {
        "thinking": {"type": "enabled"}
    }
    messages = provider_kwargs["messages"]
    assert messages == adapter_calls[0]["kwargs"]["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"][0] == {
        "type": "text",
        "text": (
            "请识别图片中学生选择题最终选项。只返回 JSON：\n"
            '{"selected":"A|B|C|D|E|F|blank|multiple|unclear",'
            '"confidence":0-1,"need_review":true/false,'
            '"review_reason":""}\n'
            "不要解释，不要评分。\n"
            "只识别学生作答痕迹，例如圈选、勾选、涂黑、手写标记。\n"
            "不要把图片中印刷的 A/B/C/D 选项文字当作学生选择。\n"
            "如果图片中出现多个题目的选项或无法判断学生标记，返回 unclear。"
        ),
    }
    assert messages[0]["content"][1]["type"] == "image_url"
    assert messages[0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )

    assert result["selected"] == "C"
    assert result["is_correct"] is True
    assert result["score"] == 8
    assert result["confidence"] == 0.96
    assert result["need_review"] is False
    assert result["review_reason"] == ""
    assert result["auto_scored"] is True
    assert result["prompt_tokens"] == 11
    assert result["completion_tokens"] == 7
    assert result["reasoning_tokens"] == 3
    assert result["total_tokens"] == 18
    assert result["success"] is True
    assert result["error_type"] == ""
    assert workspace_usage_writes == []


def test_choice_recognition_defaults_production_config_max_tokens_to_100(
    monkeypatch,
    tmp_path,
) -> None:
    provider_calls: list[dict[str, object]] = []
    profile = {
        "name": "fake-production-profile",
        "objective_enabled": True,
        "objective_api_key": "fake-production-key",
        "objective_base_url": "https://production-provider.invalid/v1",
        "objective_model": "fake-production-model",
        "objective_temperature": 0.15,
        "objective_thinking_type": "disabled",
        "llm_recognition_timeout_seconds": 23,
        "llm_recognition_max_retries": 0,
        "llm_recognition_requests_per_minute": 5000,
    }
    fake_store = SimpleNamespace(load=lambda: [profile])
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"selected":"B","confidence":0.97,'
                        '"need_review":false,"review_reason":""}'
                    )
                )
            )
        ],
        usage=None,
    )

    def fake_provider_create(**kwargs: object) -> object:
        provider_calls.append(kwargs)
        return completion

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_provider_create)
        )
    )
    real_adapter_class = backend_llm.LLMProtocolAdapter

    class IsolatedProtocolAdapter:
        def __init__(
            self,
            api_key: str,
            base_url: str,
            policy_profile=None,
        ) -> None:
            self._adapter = real_adapter_class(
                api_key,
                base_url,
                policy_profile=policy_profile,
                client=fake_client,
                usage_sink_factory=backend_llm.NullUsageSink,
                trace_sink_factory=backend_llm.NullCallTraceSink,
            )

        def chat_completions(self, **kwargs: object) -> object:
            return self._adapter.chat_completions(**kwargs)

    def forbid_direct_sdk(*args: object, **kwargs: object) -> object:
        raise AssertionError("direct OpenAI SDK boundary is still in use")

    monkeypatch.setattr(api_profiles, "get_api_profile_store", lambda: fake_store)
    monkeypatch.setattr(
        backend_llm,
        "LLMProtocolAdapter",
        IsolatedProtocolAdapter,
    )
    monkeypatch.setattr(openai, "OpenAI", forbid_direct_sdk)
    monkeypatch.setattr(
        usage_logger,
        "log_llm_usage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("workspace usage must not be logged")
        ),
    )
    production_config = api_profiles.get_objective_api_config()
    assert "max_tokens" not in production_config

    image_path = tmp_path / "production-choice.jpg"
    Image.new("RGB", (32, 24), "white").save(image_path, format="JPEG")

    result = recognize_choice_answer(
        image_path=image_path,
        question_id="choice-production-default",
        standard_answer="B",
        max_score=6,
        session_id="fake-production-session",
        student_id="fake-production-student",
    )

    assert provider_calls, result["error_type"]
    assert provider_calls[0]["max_tokens"] == 100
    assert result["max_tokens"] == 100
    assert result["selected"] == "B"
    assert result["is_correct"] is True
    assert result["score"] == 6
    assert result["auto_scored"] is True
    assert result["need_review"] is False
    assert result["success"] is True
    assert result["error_type"] == ""
