from __future__ import annotations

import base64
import math
from types import SimpleNamespace

import backend.llm as backend_llm
import fill_blank_recognition_chain as fill_blank_chain
import openai
import usage_logger
from PIL import Image


def _run_fill_blank_recognition(
    monkeypatch,
    tmp_path,
    *,
    model_override: str | None = None,
) -> dict[str, object]:
    provider_calls: list[dict[str, object]] = []
    adapter_init_calls: list[dict[str, object]] = []
    adapter_calls: list[dict[str, object]] = []
    workspace_usage_writes: list[dict[str, object]] = []
    policy_profile = {
        "llm_recognition_timeout_seconds": 41,
        "llm_recognition_max_retries": 0,
        "llm_recognition_requests_per_minute": 5000,
    }
    config = {
        "enabled": True,
        "model": "fake-fill-model",
        "api_key": "fake-fill-key",
        "base_url": "https://provider.invalid/v1",
        "timeout": 999,
        "temperature": 0.25,
        "max_tokens": 177,
        "thinking_type": "enabled",
        "policy_profile": policy_profile,
    }
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"raw_answer":"1/2","confidence":0.96,'
                        '"need_review":false,"review_reason":""}'
                    )
                )
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=13,
            completion_tokens=8,
            total_tokens=21,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
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
            )

        def chat_completions(self, **kwargs: object) -> object:
            adapter_calls.append(kwargs)
            return self._adapter.chat_completions(**kwargs)

    def forbid_direct_sdk(*args: object, **kwargs: object) -> object:
        raise AssertionError("direct OpenAI SDK boundary is still in use")

    monkeypatch.setattr(
        fill_blank_chain,
        "get_objective_api_config",
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
    image_path = tmp_path / "fill-blank.jpg"
    Image.new("RGB", (36, 24), "white").save(image_path, format="JPEG")

    result = fill_blank_chain.recognize_fill_blank_answer(
        image_path=str(image_path),
        question_id="fill-3",
        standard_answer="0.5",
        max_score=6,
        model=model_override,
        session_id="fake-session",
        student_id="fake-student",
    )

    return {
        "adapter_calls": adapter_calls,
        "adapter_init_calls": adapter_init_calls,
        "config": config,
        "image_path": image_path,
        "policy_profile": policy_profile,
        "provider_calls": provider_calls,
        "result": result,
        "workspace_usage_writes": workspace_usage_writes,
    }


def test_fill_blank_recognition_routes_existing_request_through_gateway(
    monkeypatch,
    tmp_path,
) -> None:
    observed = _run_fill_blank_recognition(monkeypatch, tmp_path)
    provider_calls = observed["provider_calls"]
    result = observed["result"]

    assert provider_calls, result["error_type"]
    assert observed["adapter_init_calls"] == [
        {
            "api_key": "fake-fill-key",
            "base_url": "https://provider.invalid/v1",
            "policy_profile": observed["policy_profile"],
        }
    ]
    adapter_calls = observed["adapter_calls"]
    assert len(adapter_calls) == 1
    assert (
        adapter_calls[0]["request_kind"]
        is backend_llm.LLMRequestKind.RECOGNITION
    )
    assert adapter_calls[0]["model"] == "fake-fill-model"
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
    assert provider_kwargs["model"] == "fake-fill-model"
    assert math.isfinite(provider_kwargs["timeout"])
    assert provider_kwargs["timeout"] == 41.0
    assert provider_kwargs["temperature"] == 0.25
    assert provider_kwargs["max_tokens"] == 177
    assert provider_kwargs["response_format"] == {"type": "json_object"}
    assert provider_kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    image_path = observed["image_path"]
    data_url = "data:image/jpeg;base64," + base64.b64encode(
        image_path.read_bytes()
    ).decode("utf-8")
    assert provider_kwargs["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "请识别图片中学生填空题最终答案。只返回 JSON：\n"
                        '{"raw_answer":"", "confidence":0-1, '
                        '"need_review":true/false, "review_reason":""}\n'
                        "不要解释，不要评分。"
                    ),
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    assert provider_kwargs["messages"] == adapter_calls[0]["kwargs"]["messages"]

    assert result["raw_answer"] == "1/2"
    assert result["normalized_student_answer"] == "1/2"
    assert result["normalized_standard_answer"] == "0.5"
    assert result["match_status"] == "equivalent"
    assert result["is_correct"] is True
    assert result["score"] == 6
    assert result["auto_scored"] is True
    assert result["confidence"] == 0.96
    assert result["need_review"] is False
    assert result["review_reason"] == ""
    assert result["prompt_tokens"] == 13
    assert result["completion_tokens"] == 8
    assert result["reasoning_tokens"] == 4
    assert result["total_tokens"] == 21
    assert result["success"] is True
    assert result["error_type"] == ""
    assert observed["workspace_usage_writes"] == []


def test_fill_blank_model_argument_overrides_only_provider_model(
    monkeypatch,
    tmp_path,
) -> None:
    observed = _run_fill_blank_recognition(
        monkeypatch,
        tmp_path,
        model_override="override-fill-model",
    )
    provider_calls = observed["provider_calls"]
    result = observed["result"]

    assert provider_calls, result["error_type"]
    adapter_call = observed["adapter_calls"][0]
    provider_kwargs = provider_calls[0]
    assert adapter_call["model"] == "override-fill-model"
    assert provider_kwargs["model"] == "override-fill-model"
    assert result["model"] == "override-fill-model"
    assert result["configured_objective_model"] == "override-fill-model"
    assert result["actual_model_used"] == "override-fill-model"
    assert provider_kwargs["temperature"] == 0.25
    assert provider_kwargs["max_tokens"] == 177
    assert provider_kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert observed["adapter_init_calls"] == [
        {
            "api_key": "fake-fill-key",
            "base_url": "https://provider.invalid/v1",
            "policy_profile": observed["policy_profile"],
        }
    ]
    assert observed["config"]["model"] == "fake-fill-model"
    assert observed["workspace_usage_writes"] == []
